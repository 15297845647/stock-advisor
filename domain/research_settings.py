"""Deployment switch is explicit; invalid values fail closed."""
import os


def research_enabled():
    mode=os.getenv('STOCK_ANALYSIS_ENGINE','legacy')
    if mode not in ('legacy','v2'): raise ValueError('STOCK_ANALYSIS_ENGINE must be legacy or v2')
    return mode=='v2'
