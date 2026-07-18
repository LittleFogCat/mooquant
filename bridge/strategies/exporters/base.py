"""策略导出器基类，定义导出契约。"""


class ExporterBase:
    """导出器基类：子类实现 export()，将策略类转为目标平台脚本字符串。"""

    PLATFORM = ""

    def export(self, strategy_class, params=None):
        """导出策略为目标平台脚本字符串。

        Args:
            strategy_class: StrategyBase 子类
            params: 可选，覆盖默认参数
        Returns:
            str: 目标平台可运行的单文件脚本
        """
        raise NotImplementedError
