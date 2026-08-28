# mookquant * Model trainer

import os
import sys
import json
import math
import random
import time


# --- DataFetcher interface (dependency injection for testability) ---

class DataFetcher:
    # Interface for fetching historical bar data.
    def fetch_bars(self, symbol, period, count):
        # Returns list of bar dicts: [{date, open, high, low, close, volume}, ...]
        raise NotImplementedError


class XtdataDataFetcher(DataFetcher):
    # Fetches data via the unified datafeed (single source of truth).
    # 口径（v2，见 bridge/data/datafeed.py）：volume=股（×100）、front_ratio 前复权比例版。
    # 已废弃直接调用 xtquant 的旧实现（曾导致训练/回测口径漂移）。
    def fetch_bars(self, symbol, period, count):
        from data import datafeed
        bars, _source = datafeed.fetch_bars(symbol, period=period, count=count,
                                            dividend_type="front_ratio")
        return bars

    def fetch_bars_range(self, symbol, period, start_date, end_date):
        # 按时间区间取数（训练范围选择）：与 count 模式共用统一 datafeed，
        # 口径（单位/复权）完全一致
        from data import datafeed
        bars, _source = datafeed.fetch_bars(symbol, period=period,
                                            start_date=start_date, end_date=end_date,
                                            dividend_type="front_ratio")
        return bars


class MockDataFetcher(DataFetcher):
    # Returns pre-generated random bars for testing.
    def fetch_bars(self, symbol, period, count):
        bars = []
        price = 35.0
        for i in range(count):
            change = random.gauss(0, 0.02)
            o = price
            c = max(0.01, price * (1 + change))
            h = max(o, c) * 1.005
            l = min(o, c) * 0.995
            v = random.randint(500000, 2000000)
            bars.append({'date': f'2026-01-{i+1:02d}', 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v})
            price = c
        return bars

    def fetch_bars_range(self, symbol, period, start_date, end_date):
        # 按区间生成模拟数据（与 fetch_bars 同分布，日期落在区间内）
        from _shared import generate_mock_bars
        return generate_mock_bars(symbol, start_date, end_date)


def _default_data_fetcher():
    """优先 xtdata 真实数据，不可用/无数据时回退 mock（与回测引擎一致）。

    避免训练用模拟随机数据、回测用真实行情导致分布不一致。
    """
    try:
        fetcher = XtdataDataFetcher()
        if fetcher.fetch_bars('600036.SH', '1d', 5):
            return fetcher
    except Exception:
        pass
    return MockDataFetcher()


# --- Label function mapping ---

LABEL_FNS = {
    'classification': 'make_classification_labels',
    'regression': 'make_regression_labels',
    'triple_barrier': 'make_triple_barrier_labels',
}


def is_classification_task(label_type):
    # 分类标签（含三重障碍法）用交叉熵 + 类别均衡采样；回归用 MSE
    return label_type in ('classification', 'triple_barrier')


class Trainer:
    # Complete training pipeline: data -> features -> labels -> model -> train -> save.

    def __init__(self, data_fetcher=None, on_progress=None):
        self.data_fetcher = data_fetcher or _default_data_fetcher()
        # on_progress(progress: 0-100, stage: str) 实时进度回调（可空）
        self.on_progress = on_progress

    def _report(self, progress, stage):
        if self.on_progress:
            try:
                self.on_progress(progress, stage)
            except Exception:
                pass

    def train(self, config):
        # Execute full training. Returns result dict.
        import torch
        from torch.utils.data import DataLoader
        from strategies.ml.features import FeatureBuilder
        from strategies.ml.models.registry import build_model
        from training.dataset import FinancialDataset
        from training.labels import (make_classification_labels,
            make_regression_labels, make_triple_barrier_labels)
        from training.model_registry import ModelRegistry

        # 1. Parse config
        data_cfg = config.get('data', {}) or {}
        feat_cfg = config.get('feature_config', {})
        # 空特征配置给一组合理默认（v2 尺度不变特征）：
        # 裸 close/volume 依赖价格/单位尺度，口径漂移会造成分布外输入；
        # 默认改为 log_volume/量比/MA偏离度/振幅等无量纲特征 + 技术指标
        if not feat_cfg:
            feat_cfg = {
                'window': 20,
                'raw_features': [],
                'indicators': [
                    {'name': 'macd', 'params': {}},
                    {'name': 'rsi', 'params': {'period': 14}},
                    {'name': 'boll', 'params': {'period': 20}},
                    {'name': 'kdj', 'params': {}},
                ],
                'derived': ['return_1d', 'return_5d', 'log_volume', 'volume_ratio_5d',
                            'ma_deviation', 'high_low_range', 'close_return'],
                'normalize': 'zscore',
                'normalize_mode': 'global',
                'normalize_window': 20,
            }
        label_cfg = config.get('label_config', {})
        # 未显式指定阈值时，用更平衡的默认阈值：低波动标的下默认 0.02 会
        # 产生 ~57% 的 flat 标签，模型退化为恒判 flat，永远不产生交易信号
        if label_cfg.get('type', 'classification') in ('classification',) and 'threshold' not in label_cfg:
            label_cfg = dict(label_cfg)
            label_cfg['threshold'] = 0.01
        model_arch = config.get('model_arch', 'lstm')
        model_params = config.get('model_params', {})
        train_cfg = config.get('train_config', {})
        model_name = (config.get('model_name', '') or '').strip()
        if not model_name:
            # 自动生成有意义的默认名：架构_日期时间
            model_name = '{}_{}'.format(
                config.get('model_arch', 'model'),
                time.strftime('%Y%m%d_%H%M%S'))

        symbols = data_cfg.get('symbols', ['600036.SH'])
        period = data_cfg.get('period', '1d')
        count = data_cfg.get('count', 500)
        start_date = data_cfg.get('start_date', '')
        end_date = data_cfg.get('end_date', '')

        epochs = train_cfg.get('epochs', 50)
        batch_size = train_cfg.get('batch_size', 32)
        lr = train_cfg.get('learning_rate', 0.001)
        val_ratio = train_cfg.get('val_ratio', 0.2)
        patience = train_cfg.get('patience', 10)

        # 2. Fetch data（无数据的标的自动跳过并记录，避免训练集空跑）
        self._report(5, 'fetch')
        bars_list = []
        skipped_symbols = []
        use_range = bool(start_date and end_date)
        for sym in symbols:
            if use_range:
                bars = self.data_fetcher.fetch_bars_range(sym, period, start_date, end_date)
            else:
                bars = self.data_fetcher.fetch_bars(sym, period, count)
            if bars and len(bars) > 0:
                bars_list.append(bars)
            else:
                skipped_symbols.append(sym)

        if not bars_list:
            raise RuntimeError('No data fetched: 全部标的均无数据，请更换标的或检查数据源')

        self._fetched_symbols = [s for s in symbols if s not in skipped_symbols]
        self._skipped_symbols = skipped_symbols
        if skipped_symbols:
            self._report(8, 'fetch')  # 轻微推进，提示跳过处理中

        # 2a. 数据体检摘要（供 UI 展示与日志留档）
        from data import datafeed
        data_summary = [datafeed.summary(bars, sym) for sym, bars in
                        zip(self._fetched_symbols, bars_list)]
        data_problems = []
        for sym, bars in zip(self._fetched_symbols, bars_list):
            data_problems.extend(datafeed.validate_bars(bars, sym))

        # 3. Build features + labels + dataset
        self._report(15, 'build')
        # 默认启用 global 归一化（跨样本有区分度），仅当显式指定 rolling 才保留旧行为
        if feat_cfg.get('normalize_mode') not in ('global', 'rolling'):
            feat_cfg = dict(feat_cfg)
            feat_cfg['normalize_mode'] = 'global'
        fb = FeatureBuilder(feat_cfg)
        label_type = label_cfg.get('type', 'classification')
        label_fn_name = LABEL_FNS.get(label_type, 'make_classification_labels')
        label_fn = {
            'make_classification_labels': make_classification_labels,
            'make_regression_labels': make_regression_labels,
            'make_triple_barrier_labels': make_triple_barrier_labels,
        }[label_fn_name]
        label_params = {k: v for k, v in label_cfg.items() if k != 'type'}

        # 3a. 全局归一化统计量：对所有标的的全量 bars 统一 fit，训练/推理共用同一套统计
        #     （避免多标的各自滚动归一化导致特征尺度不一致）
        global_stats = None
        if feat_cfg.get('normalize_mode') == 'global':
            all_matrix = None
            for bars in bars_list:
                m = fb._build_matrix(bars)
                if m:
                    if all_matrix is None:
                        all_matrix = m
                    else:
                        all_matrix = all_matrix + m
            if all_matrix:
                global_stats = fb._fit_stats(all_matrix)
                fb._global_stats = global_stats

        dataset = FinancialDataset(fb, bars_list, label_fn, label_params)
        if len(dataset) == 0:
            raise RuntimeError('Empty dataset')

        # 3b. 标签分布预览（训练结果与体检报告共用）
        import collections
        label_dist = dict(collections.Counter(dataset.y.tolist()))
        if is_classification_task(label_type):
            # 整数类别 key 转 str（JSON 友好）
            label_dist = {str(k): v for k, v in sorted(label_dist.items())}

        # 4. Time-series split (no shuffle)
        n = len(dataset)
        n_val = max(1, int(n * val_ratio))
        n_train = n - n_val
        train_ds = torch.utils.data.Subset(dataset, range(n_train))
        val_ds = torch.utils.data.Subset(dataset, range(n_train, n))
        # 分类任务：抽样时打乱（窗口样本非严格时序，打乱利于 SGD 收敛），
        # 不做激进反频采样（实测会压迫模型偏押单类），用轻量类别权重即可
        shuffle_train = is_classification_task(label_type)
        train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=shuffle_train)
        val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)

        # 5. Build model
        self._report(20, 'build')
        is_classification = label_type in ('classification', 'triple_barrier')
        task = 'classification' if is_classification else 'regression'
        output_size = label_cfg.get('output_size', 3 if is_classification else 1)
        model_params.setdefault('task', task)
        model_params.setdefault('output_size', output_size)
        model_params.setdefault('input_size', fb.n_features)
        # 各架构参数名不同：UI 统一传 hidden_size，按架构映射，避免
        # MLP/Transformer 因多余的 hidden_size 关键字报 TypeError
        if model_arch in ('mlp', 'gbdt'):
            model_params.setdefault('seq_len', feat_cfg.get('window', 20))
        hs = model_params.pop('hidden_size', None)
        if hs:
            if model_arch == 'mlp':
                model_params.setdefault('hidden_sizes', [hs])
            elif model_arch == 'transformer':
                model_params.setdefault('d_model', hs)
            elif model_arch == 'gbdt':
                pass  # 树模型无 hidden_size（防多余参数报错）
            else:
                model_params['hidden_size'] = hs
        model = build_model(model_arch, model_params)
        is_sklearn = bool(getattr(model, 'is_sklearn', False))  # sklearn 基线走独立训练/持久化

        # 6. Training
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        best_val_loss = float('inf')
        best_train_loss = 0.0
        best_state = None
        best_val_accuracy = 0.0  # 保存 best_state 时对应的 val 准确率（指标与模型一致）
        no_improve = 0
        train_log = []

        if is_sklearn:
            # ---- sklearn 表格基线（GBDT）：无 torch 训练循环，直接 fit + 验证 ----
            self._report(30, 'train')
            X_all, y_all = dataset.X, dataset.y
            model.fit(X_all[:n_train], y_all[:n_train])
            if n_train < len(X_all):
                proba_va = model.predict_proba(X_all[n_train:])
                pred_va = proba_va.argmax(axis=1)
                best_val_accuracy = float((pred_va == y_all[n_train:].numpy().ravel()).mean())
            best_train_loss = 0.0
            best_val_loss = 0.0
            best_state = 'sklearn'  # 占位：sklearn 持久化走 model_type=sklearn 分支
            train_log.append({'epoch': 1, 'train_loss': 0, 'val_loss': 0,
                              'accuracy': round(best_val_accuracy, 4)})
            self._report(95, 'train')
        else:
            # ---- torch 训练循环 ----
            model.to(device)
            # 类别权重：分类任务用「频率的 sqrt 反比」做轻量加权（flat 略降权、buy/sell 略升权），
            # 实测激进反频加权会压迫模型偏押单类（all-buy/all-sell），轻量加权更稳
            class_weights = None
            if is_classification:
                import collections
                cnt = collections.Counter(dataset.y.tolist())
                if len(cnt) > 1:
                    n = sum(cnt.values())
                    class_weights = torch.tensor(
                        [math.sqrt(n / (len(cnt) * max(cnt.get(c, 0), 1))) for c in range(max(cnt) + 1)],
                        dtype=torch.float32).to(device)
            criterion = torch.nn.CrossEntropyLoss(weight=class_weights) if is_classification else torch.nn.MSELoss()
            optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
            scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)

            # 训练阶段进度占 20% -> 95%，按 epoch 推进
            progress_span = 95 - 20
            for epoch in range(epochs):
                self._report(20 + progress_span * (epoch + 1) / epochs, 'train')
                model.train()
                total_loss = 0
                n_batches = 0
                for X, y in train_loader:
                    X, y = X.to(device), y.to(device)
                    optimizer.zero_grad()
                    out = model(X)
                    loss = criterion(out, y)
                    loss.backward()
                    optimizer.step()
                    total_loss += loss.item()
                    n_batches += 1
                train_loss = total_loss / max(1, n_batches)

                model.eval()
                val_loss, val_metric = self._evaluate(model, val_loader, criterion, device, is_classification)
                scheduler.step(val_loss)

                log_entry = {'epoch': epoch+1, 'train_loss': round(train_loss, 6),
                             'val_loss': round(val_loss, 6), 'lr': optimizer.param_groups[0]['lr']}
                if is_classification:
                    log_entry['accuracy'] = round(val_metric, 4)
                train_log.append(log_entry)

                if val_loss < best_val_loss:
                    best_val_loss = val_loss
                    best_train_loss = train_loss
                    best_val_accuracy = val_metric  # 与 best_state 同步记录
                    best_state = {k: v.clone() for k, v in model.state_dict().items()}
                    no_improve = 0
                else:
                    no_improve += 1
                    if no_improve >= patience:
                        break

        # 7. Restore best model and save
        if best_state and not is_sklearn:
            model.load_state_dict(best_state)
        self._report(98, 'save')

        metrics = {
            'train_loss': round(best_train_loss, 6) if train_log else 0,
            'val_loss': round(best_val_loss, 6),
            'n_samples': n,
        }
        # accuracy 必须对应「最终保存的 best_state」的 val 准确率，
        # 而非最后一个 epoch（两者常不是同一状态，会误导用户）
        if is_classification:
            metrics['accuracy'] = round(best_val_accuracy, 4)

        # 7a. 体检报告（M3.3）：验证集混淆矩阵 + 概率分布 + 质量评分
        health_report = None
        if is_classification:
            if is_sklearn:
                health_report = self._health_report_sklearn(model, dataset.X[n_train:], dataset.y[n_train:])
            else:
                health_report = self._health_report(model, val_loader, device, dataset.y.tolist())

        full_config = {
            'model_arch': model_arch,
            'model_params': model_params,
            'feature_config': feat_cfg,
            'label_config': label_cfg,
            'train_config': train_cfg,
            'data': data_cfg,
        }
        if is_sklearn:
            full_config['model_type'] = 'sklearn'  # 持久化/加载分支：pickle 整体保存
        # 数据口径指纹 + 训练数据日期范围：推理/回测时校验，防口径漂移与样本内回测
        full_config['data_fingerprint'] = datafeed.data_fingerprint('front_ratio')
        full_config['data_date_range'] = {
            'start': min((b['date'][:10] for bars in bars_list for b in bars[:1]), default=''),
            'end': max((b['date'][:10] for bars in bars_list for b in bars[-1:]), default=''),
        }
        # 数据体检摘要 + 质量问题（UI 可展示）
        full_config['data_summary'] = data_summary
        if data_problems:
            full_config['data_problems'] = data_problems
        # 标签分布（体检报告用）
        if label_dist:
            full_config['label_dist'] = label_dist
        # 持久化全局归一化统计量：推理重建 FeatureBuilder 时用同一套统计
        if global_stats is not None:
            full_config['feature_stats'] = global_stats
        # 记录实际用于训练的标的（无数据标的已剔除），供 UI 展示
        data_cfg = dict(data_cfg)
        data_cfg['symbols'] = self._fetched_symbols
        full_config['data'] = data_cfg
        skipped_symbols = getattr(self, '_skipped_symbols', [])

        # 体检报告与质量评分：随 config 持久化 + 返回给 UI
        if health_report:
            full_config['health_report'] = health_report
            metrics['quality_score'] = health_report.get('score', 0)
            metrics['degraded'] = health_report.get('grade') == 'red'

        # D3.4：save_model=False 时仅返回指标不落盘（walk-forward 折叠用）
        if config.get('save_model', True):
            model_id = ModelRegistry.save(model, full_config, metrics, model_name)
        else:
            model_id = ''

        result = {
            'model_id': model_id,
            'metrics': metrics,
            'train_log': train_log,
            'n_samples': n,
            'feature_config': feat_cfg,
            'model_config': model_params,
            'symbols': self._fetched_symbols,
            'skipped_symbols': skipped_symbols,
            'data_summary': data_summary,
            'data_problems': data_problems,
            'label_dist': label_dist,
            'health_report': health_report,
        }
        # D3.4：return_model=True 时带回内存模型与特征构建器（walk-forward 样本外评估用）
        if config.get('return_model', False):
            result['_model'] = model
            result['_feature_builder'] = fb

        # D3.4：walk-forward 滚动样本外评估（防过拟合，随训练结果返回稳定性报告）
        if config.get('walk_forward'):
            try:
                result['walk_forward'] = self.walk_forward(
                    config, n_segments=int(config.get('walk_forward_segments', 3) or 3))
            except Exception as e:
                result['walk_forward_error'] = str(e)

        return result

    # ------------------------------------------------------------------
    # D3.4 walk-forward 滚动样本外评估（防过拟合核心）
    # ------------------------------------------------------------------
    def walk_forward(self, config, n_segments=3):
        """数据按时间等分 n_segments 段；对每段 i≥1，用「该段之前」的数据训练、
        该段做样本外评估（不参与训练），输出各段 accuracy 的均值/方差与稳定性结论。

        - 折叠训练走 `self.train()`（save_model=False / return_model=True，不落盘）。
        - 全部子训练统一走「区间模式」（count 模式先取一次转为有效区间），
          保证折叠训练数据与分段数据同源一致。
        - 仅支持分类标签（classification / triple_barrier）。

        Returns:
            {folds, meanAccuracy, stdAccuracy, nFolds, stable, verdict, summary}
        """
        import copy
        import numpy as np
        from strategies.ml.features import FeatureBuilder
        from training.labels import (make_classification_labels, make_regression_labels,
                                     make_triple_barrier_labels)

        data_cfg = config.get('data', {}) or {}
        symbols = data_cfg.get('symbols', ['600036.SH'])
        period = data_cfg.get('period', '1d')
        start_date = data_cfg.get('start_date', '')
        end_date = data_cfg.get('end_date', '')
        count = data_cfg.get('count', 0)

        label_cfg = config.get('label_config', {}) or {}
        label_type = label_cfg.get('type', 'classification')
        if not is_classification_task(label_type):
            raise ValueError('walk-forward 当前仅支持分类标签（classification / triple_barrier）')

        # 取第一个有数据的标的做分段评估（多标的下近似）
        sym = None
        probe_bars = []
        for s in symbols:
            if start_date and end_date:
                b = self.data_fetcher.fetch_bars_range(s, period, start_date, end_date)
            else:
                b = self.data_fetcher.fetch_bars(s, period, count or 0)
            if b:
                sym, probe_bars = s, b
                break
        if sym is None or not probe_bars:
            raise RuntimeError('No data fetched')
        # 统一转区间模式，保证折叠训练与分段同源
        if not (start_date and end_date):
            start_date = probe_bars[0]['date'][:10]
            end_date = probe_bars[-1]['date'][:10]
        bars = self.data_fetcher.fetch_bars_range(sym, period, start_date, end_date)
        if not bars or len(bars) < 10:
            raise RuntimeError('walk-forward 数据不足')

        n = len(bars)
        seg_size = n // n_segments
        if seg_size < 20:
            raise ValueError('数据量不足以做 walk-forward（每段至少 20 根，当前 {}/{}）'.format(seg_size, n_segments))

        label_fn_name = LABEL_FNS.get(label_type, 'make_classification_labels')
        label_fn = {
            'make_classification_labels': make_classification_labels,
            'make_regression_labels': make_regression_labels,
            'make_triple_barrier_labels': make_triple_barrier_labels,
        }[label_fn_name]
        label_params = {k: v for k, v in label_cfg.items() if k != 'type'}

        folds = []
        for i in range(1, n_segments):
            train_end = i * seg_size
            test_end = n if i == n_segments - 1 else (i + 1) * seg_size
            test_bars = bars[train_end:test_end]
            if len(test_bars) < 5:
                continue
            fold_cfg = copy.deepcopy(config)
            fold_cfg['data'] = dict(data_cfg)
            fold_cfg['data']['symbols'] = [sym]
            fold_cfg['data']['start_date'] = bars[0]['date'][:10]
            fold_cfg['data']['end_date'] = bars[train_end - 1]['date'][:10]
            fold_cfg['data'].pop('count', None)
            fold_cfg['save_model'] = False
            fold_cfg['return_model'] = True
            fold_cfg['walk_forward'] = False

            r = self.train(fold_cfg)
            model = r.get('_model')
            fb = r.get('_feature_builder')
            if model is None or fb is None:
                continue
            X = fb.build_batch(test_bars)
            labels = label_fn(test_bars, **label_params)
            offset = fb.window - 1
            m = min(len(X), len(labels) - offset)
            if m <= 0:
                continue
            pred = self._predict_labels(model, X[:m])
            true = np.asarray(labels[offset:offset + m])
            acc = float(np.mean(pred == true))
            f1_0 = self._class_f1(pred, true, 0)
            f1_2 = self._class_f1(pred, true, 2)
            direction_f1 = ((f1_0 + f1_2) / 2) if (f1_0 is not None and f1_2 is not None) else None
            folds.append({
                'fold': i,
                'trainRange': '{} ~ {}'.format(bars[0]['date'][:10], bars[train_end - 1]['date'][:10]),
                'testRange': '{} ~ {}'.format(bars[train_end]['date'][:10], bars[test_end - 1]['date'][:10]),
                'accuracy': round(acc, 4),
                'directionF1': round(direction_f1, 4) if direction_f1 is not None else None,
                'nTest': int(m),
            })

        if not folds:
            raise RuntimeError('walk-forward 无有效折叠（数据量或标签不足）')

        accs = [f['accuracy'] for f in folds]
        mean_acc = float(np.mean(accs))
        std_acc = float(np.std(accs))
        if std_acc <= 0.05:
            stable = '稳定'
        elif std_acc <= 0.15:
            stable = '一般'
        else:
            stable = '不稳定（过拟合风险）'
        verdict = 'green' if (std_acc <= 0.05 and mean_acc >= 0.4) else ('yellow' if std_acc <= 0.15 else 'red')
        summary = 'walk-forward {} 段样本外：准确率 {:.1%} ± {:.1%}（{}）'.format(
            len(folds), mean_acc, std_acc, stable)
        return {
            'folds': folds,
            'meanAccuracy': round(mean_acc, 4),
            'stdAccuracy': round(std_acc, 4),
            'nFolds': len(folds),
            'stable': stable,
            'verdict': verdict,
            'summary': summary,
        }

    @staticmethod
    def _predict_labels(model, X):
        """模型预测类别标签（兼容 torch 与 sklearn，D3.4）。"""
        import numpy as np
        if getattr(model, 'is_sklearn', False):
            return model.predict_proba(X).argmax(axis=1)
        import torch
        device = next(model.parameters()).device
        X = X.to(device)
        with torch.no_grad():
            out = model(X)
            probs = torch.softmax(out, dim=-1)
            return probs.argmax(dim=1).cpu().numpy()

    @staticmethod
    def _class_f1(pred, true, cls):
        """单类别 F1（无该类正样本或预测时返回 None）。"""
        tp = int(((pred == cls) & (true == cls)).sum())
        fp = int(((pred == cls) & (true != cls)).sum())
        fn = int(((pred != cls) & (true == cls)).sum())
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        if precision + recall <= 0:
            return None
        return 2 * precision * recall / (precision + recall)

    def _health_report(self, model, val_loader, device, all_labels):
        """训练体检报告（M3.3）：验证集混淆矩阵、概率分布、质量评分。

        评分规则（0-100）：
          - 方向类 F1（buy/sell 两类的 macro-F1）为主项：权重 60
          - 预测多样性（非 flat 预测占比）与真实分布一致性：权重 20
          - 概率置信度有效性（max prob 与正确率相关性简化项）：权重 20
        等级：>=60 green / >=35 yellow / else red（red = degraded，UI 禁止激活）
        """
        import torch
        import collections
        model.eval()
        confusion = [[0, 0, 0] for _ in range(3)]
        max_probs = []
        correct_flags = []
        with torch.no_grad():
            for X, y in val_loader:
                X, y = X.to(device), y.to(device)
                out = model(X)
                probs = torch.softmax(out, dim=-1)
                conf, pred = probs.max(dim=1)
                for yi, pi, ci in zip(y.tolist(), pred.tolist(), conf.tolist()):
                    if 0 <= yi < 3 and 0 <= pi < 3:
                        confusion[yi][pi] += 1
                max_probs.extend(conf.tolist())
                correct_flags.extend([1 if a == b else 0 for a, b in zip(y.tolist(), pred.tolist())])

        n_val = sum(sum(row) for row in confusion)
        if n_val == 0:
            return None
        return self._score_health(confusion, max_probs, correct_flags)

    def _score_health(self, confusion, max_probs, correct_flags):
        """根据混淆矩阵/置信度/正确标记计算体检评分（torch 与 sklearn 基线共用）。"""

        n_val = sum(sum(row) for row in confusion)
        if n_val == 0:
            return None

        # 混淆矩阵 -> 各类 precision/recall/f1
        f1s = []
        details = {}
        for c in range(3):
            tp = confusion[c][c]
            fp = sum(confusion[r][c] for r in range(3)) - tp
            fn = sum(confusion[c]) - tp
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
            f1s.append(f1)
            details['class_' + str(c)] = {
                'precision': round(precision, 4), 'recall': round(recall, 4), 'f1': round(f1, 4),
            }
        # 方向类（0=sell, 2=buy）macro-F1；flat 类（1）单列参考
        direction_f1 = (f1s[0] + f1s[2]) / 2

        # 概率分布直方图（10 桶，诊断退化：全部挤在一侧说明模型无区分度）
        hist = [0] * 10
        for p in max_probs:
            b = min(9, int(p * 10))
            hist[b] += 1

        # 预测多样性：argmax 落在非 flat 的比例
        pred_counts = [sum(confusion[r][c] for r in range(3)) for c in range(3)]
        non_flat_pred_ratio = (pred_counts[0] + pred_counts[2]) / n_val
        true_non_flat_ratio = (confusion[0][0] + confusion[0][1] + confusion[0][2] +
                               confusion[2][0] + confusion[2][1] + confusion[2][2]) / n_val
        diversity = 1.0 - abs(non_flat_pred_ratio - true_non_flat_ratio)

        # 置信度有效性：高置信桶正确率应高于低置信桶（简化：整体正确率 vs max>0.5 的正确率）
        acc_all = sum(correct_flags) / len(correct_flags) if correct_flags else 0
        hi = [(f, p) for f, p in zip(correct_flags, max_probs) if p > 0.5]
        acc_hi = sum(f for f, _ in hi) / len(hi) if hi else acc_all
        confidence_gain = max(0.0, min(1.0, (acc_hi - acc_all) * 5))  # 0.2 增益即满分

        score = round(direction_f1 * 60 + diversity * 20 + confidence_gain * 20)
        grade = 'green' if score >= 60 else ('yellow' if score >= 35 else 'red')

        return {
            'score': score,
            'grade': grade,
            'degraded': grade == 'red',
            'direction_f1': round(direction_f1, 4),
            'f1_macro': round(sum(f1s) / 3, 4),
            'class_details': details,
            'confusion_matrix': confusion,
            'prob_hist': hist,
            'n_val': n_val,
            'accuracy': round(acc_all, 4),
            'non_flat_pred_ratio': round(non_flat_pred_ratio, 4),
        }

    def _health_report_sklearn(self, model, X_va, y_va):
        """sklearn 基线体检报告（与 torch 版输出结构一致，D3.3）。"""
        proba = model.predict_proba(X_va)
        pred = proba.argmax(axis=1)
        y = y_va.numpy().ravel()
        confusion = [[0, 0, 0] for _ in range(3)]
        max_probs = []
        correct_flags = []
        for yi, pi, ci in zip(y, pred, proba.max(axis=1)):
            yi, pi = int(yi), int(pi)
            if 0 <= yi < 3 and 0 <= pi < 3:
                confusion[yi][pi] += 1
            max_probs.append(float(ci))
            correct_flags.append(1 if yi == pi else 0)
        return self._score_health(confusion, max_probs, correct_flags)

    def _evaluate(self, model, loader, criterion, device, is_classification):
        import torch
        model.eval()
        total_loss = 0
        n_batches = 0
        correct = 0
        total = 0
        with torch.no_grad():
            for X, y in loader:
                X, y = X.to(device), y.to(device)
                out = model(X)
                loss = criterion(out, y)
                total_loss += loss.item()
                n_batches += 1
                if is_classification:
                    pred = out.argmax(dim=1)
                    correct += (pred == y).sum().item()
                    total += len(y)
        avg_loss = total_loss / max(1, n_batches)
        metric = correct / max(1, total) if is_classification else 0.0
        return avg_loss, metric
