"""Synthetic fixtures for explicitly offline demos only."""
def demo_bars(symbol):
    return [{'date':f'2026-09-{i+1:02}','open':100+i,'close':101+i,'high':103+i,'low':99+i,'volume':100} for i in range(45)]
