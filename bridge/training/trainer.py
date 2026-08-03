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
    # Fetches data via xtdata (same Python env as qmt_server).
    def fetch_bars(self, symbol, period, count):
        from xtquant import xtdata
        data = xtdata.get_market_data_ex([], [symbol], period=period, count=count)
        if symbol not in data:
            return []
        df = data[symbol]
        bars = []
        for i in range(len(df)):
            row = df.iloc[i]
            bars.append({
                'date': str(row.name),
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume']),
            })
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


# --- Label function mapping ---

LABEL_FNS = {
    'classification': 'make_classification_labels',
    'regression': 'make_regression_labels',
    'triple_barrier': 'make_triple_barrier_labels',
}


class Trainer:
    # Complete training pipeline: data -> features -> labels -> model -> train -> save.

    def __init__(self, data_fetcher=None):
        self.data_fetcher = data_fetcher or MockDataFetcher()

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
        data_cfg = config.get('data', {})
        feat_cfg = config.get('feature_config', {})
        label_cfg = config.get('label_config', {})
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

        epochs = train_cfg.get('epochs', 50)
        batch_size = train_cfg.get('batch_size', 32)
        lr = train_cfg.get('learning_rate', 0.001)
        val_ratio = train_cfg.get('val_ratio', 0.2)
        patience = train_cfg.get('patience', 10)

        # 2. Fetch data
        bars_list = []
        for sym in symbols:
            bars = self.data_fetcher.fetch_bars(sym, period, count)
            if bars:
                bars_list.append(bars)

        if not bars_list:
            raise RuntimeError('No data fetched')

        # 3. Build features + labels + dataset
        fb = FeatureBuilder(feat_cfg)
        label_type = label_cfg.get('type', 'classification')
        label_fn_name = LABEL_FNS.get(label_type, 'make_classification_labels')
        label_fn = {
            'make_classification_labels': make_classification_labels,
            'make_regression_labels': make_regression_labels,
            'make_triple_barrier_labels': make_triple_barrier_labels,
        }[label_fn_name]
        label_params = {k: v for k, v in label_cfg.items() if k != 'type'}

        dataset = FinancialDataset(fb, bars_list, label_fn, label_params)
        if len(dataset) == 0:
            raise RuntimeError('Empty dataset')

        # 4. Time-series split (no shuffle)
        n = len(dataset)
        n_val = max(1, int(n * val_ratio))
        n_train = n - n_val
        train_ds = torch.utils.data.Subset(dataset, range(n_train))
        val_ds = torch.utils.data.Subset(dataset, range(n_train, n))
        train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=False)
        val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False)

        # 5. Build model
        is_classification = label_type in ('classification', 'triple_barrier')
        task = 'classification' if is_classification else 'regression'
        output_size = label_cfg.get('output_size', 3 if is_classification else 1)
        model_params.setdefault('task', task)
        model_params.setdefault('output_size', output_size)
        model_params.setdefault('input_size', fb.n_features)
        model = build_model(model_arch, model_params)

        # 6. Training loop
        device = torch.device('cpu')
        model.to(device)
        criterion = torch.nn.CrossEntropyLoss() if is_classification else torch.nn.MSELoss()
        optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)

        best_val_loss = float('inf')
        best_state = None
        no_improve = 0
        train_log = []

        for epoch in range(epochs):
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
                best_state = {k: v.clone() for k, v in model.state_dict().items()}
                no_improve = 0
            else:
                no_improve += 1
                if no_improve >= patience:
                    break

        # 7. Restore best model and save
        if best_state:
            model.load_state_dict(best_state)

        metrics = {
            'train_loss': train_log[-1]['train_loss'] if train_log else 0,
            'val_loss': round(best_val_loss, 6),
            'n_samples': n,
        }
        if is_classification and train_log:
            metrics['accuracy'] = train_log[-1].get('accuracy', 0)

        full_config = {
            'model_arch': model_arch,
            'model_params': model_params,
            'feature_config': feat_cfg,
            'label_config': label_cfg,
            'train_config': train_cfg,
            'data': data_cfg,
        }

        model_id = ModelRegistry.save(model, full_config, metrics, model_name)

        return {
            'model_id': model_id,
            'metrics': metrics,
            'train_log': train_log,
            'n_samples': n,
            'feature_config': feat_cfg,
            'model_config': model_params,
        }

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
