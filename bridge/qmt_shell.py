# -*- coding: utf-8 -*-
# mookquant QMT Shell Strategy
# Fixed strategy file for QMT. Contains NO strategy logic.
# Only: fetch bars -> POST /signal -> place orders.
# Works with both rule-based and ML strategies transparently.

import json
import requests

# === Configuration (modify as needed) ===
SERVER_URL = 'http://127.0.0.1:8765'
STRATEGY = 'ma_cross'       # Strategy type name (e.g. ma_cross, momentum, lstm_trend)
PARAMS = {}                   # Strategy params (override defaults)
                              # For ML strategies (e.g. lstm_trend), model_id is auto-filled
                              # from the active model set in model management page.
SYMBOL = '600036.SH'        # Target symbol
PERIOD = '1d'                # K-line period
COUNT = 60                    # Number of bars to fetch
ACCOUNT = ''                 # QMT account ID
# ==========================================


def init(ContextInfo):
    ContextInfo.set_universe([SYMBOL])


def handlebar(ContextInfo):
    # 1. Get bar data from QMT
    from xtquant import xtdata
    data = xtdata.get_market_data_ex([], [SYMBOL], period=PERIOD, count=COUNT)
    if SYMBOL not in data:
        return
    df = data[SYMBOL]
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

    # 2. POST to model server for signal
    try:
        resp = requests.post(SERVER_URL + '/signal', json={
            'strategy': STRATEGY,
            'bars': bars,
            'params': PARAMS,
            'symbol': SYMBOL,
        }, timeout=10)
        signal = resp.json()
    except Exception as e:
        print('[shell] signal request failed:', e)
        return

    # 3. Execute order based on signal
    action = signal.get('action', 'hold')
    if action == 'buy':
        print('[shell] BUY', SYMBOL, signal.get('reason', ''))
        _place_order(ContextInfo, 'buy')
    elif action == 'sell':
        print('[shell] SELL', SYMBOL, signal.get('reason', ''))
        _place_order(ContextInfo, 'sell')


def _place_order(ContextInfo, side):
    try:
        from xtquant import xttrader, xtconstant
        if not ACCOUNT:
            print('[shell] ACCOUNT not configured, skipping order')
            return
        if side == 'buy':
            xttrader.order_stock(ACCOUNT, SYMBOL, xtconstant.STOCK_BUY, 100, xtconstant.LATEST_PRICE, -1)
        else:
            xttrader.order_stock(ACCOUNT, SYMBOL, xtconstant.STOCK_SELL, 100, xtconstant.LATEST_PRICE, -1)
    except Exception as e:
        print('[shell] order failed:', e)
