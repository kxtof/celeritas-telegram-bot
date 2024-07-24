import json


class User_settings:
    def __init__(
        self,
        priority_fee=0.0015,
        buy_amounts=[0.5, 1, 3, 5, 10],
        buy_slippage=20,
        sell_amounts=[20, 50, 100],
        sell_slippage=20,
        confirm_trades=True,
        autobuy=False,
        autobuy_amount=0.5,
        autobuy_slippage=25,
        autosell=False,
        autosell_targets=[[None, None], [None, None]],
        autosell_slippage=40,
        mev_protection=False,
        chart_previews=False,
        min_pos_value=0,
    ):
        self.priority_fee = priority_fee
        self.buy_amounts = buy_amounts
        self.buy_slippage = buy_slippage
        self.sell_amounts = sell_amounts
        self.sell_slippage = sell_slippage
        self.confirm_trades = confirm_trades
        self.autobuy = autobuy
        self.autobuy_amount = autobuy_amount
        self.autobuy_slippage = autobuy_slippage
        self.autosell = autosell
        self.autosell_targets = autosell_targets
        self.autosell_slippage = autosell_slippage
        self.mev_protection = mev_protection
        self.chart_previews = chart_previews
        self.min_pos_value = min_pos_value

    def to_dict(self):
        return {
            "priority_fee": self.priority_fee,
            "buy_amounts": self.buy_amounts,
            "buy_slippage": self.buy_slippage,
            "sell_amounts": self.sell_amounts,
            "sell_slippage": self.sell_slippage,
            "confirm_trades": self.confirm_trades,
            "autobuy": self.autobuy,
            "autobuy_amount": self.autobuy_amount,
            "autobuy_slippage": self.autobuy_slippage,
            "autosell": self.autosell,
            "autosell_targets": self.autosell_targets,
            "autosell_slippage": self.autosell_slippage,
            "mev_protection": self.mev_protection,
            "chart_previews": self.chart_previews,
            "min_pos_value": self.min_pos_value,
        }

    def to_json(self):
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data):
        return cls(
            priority_fee=data.get("priority_fee"),
            buy_amounts=data.get("buy_amounts", [0.5, 1, 3, 5, 10]),
            buy_slippage=data.get("buy_slippage", 0.2),
            sell_amounts=data.get("sell_amounts", [0.2, 0.5, 1.0]),
            sell_slippage=data.get("sell_slippage", 0.2),
            confirm_trades=data.get("confirm_trades", True),
            autobuy=data.get("autobuy", False),
            autobuy_amount=data.get("autobuy_amount", 0.5),
            autobuy_slippage=data.get("autobuy_slippage", 1),
            autosell=data.get("autosell", False),
            autosell_targets=data.get("autosell_targets", [(-0.25, 1.0), (1.0, 0.5)]),
            autosell_slippage=data.get("autosell_slippage", 0.4),
            mev_protection=data.get("mev_protection", False),
            chart_previews=data.get("chart_previews", False),
            min_pos_value=data.get("min_pos_value", 0),
        )

    @classmethod
    def from_json(cls, json_str):
        data = json.loads(json_str)
        return cls.from_dict(data)
