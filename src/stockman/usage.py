"""Local, content-free accounting of logical provider calls; not provider invoices."""
import math
import time
from .model_providers import DEEPSEEK, GEMINI, provider_of

PROVIDER_LABELS = {GEMINI:'google', DEEPSEEK:'deepseek'}


def provider_for(model, name=None):
    provider = provider_of(model)
    if provider is not None:
        return PROVIDER_LABELS[provider]
    if isinstance(name,str):
        return next((label for prefix,label in PROVIDER_LABELS.items() if name.startswith(prefix+'-')),None)
    return None

def _count(value):
    return value if type(value) is int and 0 <= value <= 2147483647 else None

def record(store, task, role, kind, model, started, usage=None, error=None, input_rate=None, output_rate=None, extra_unknown=False, provider=None):
    usage = usage if isinstance(usage, dict) else {}
    inputs = _count(usage.get("input_tokens", usage.get("prompt_token_count")))
    outputs = _count(usage.get("output_tokens", usage.get("candidates_token_count")))
    total = _count(usage.get("total_tokens", usage.get("total_token_count")))
    if "candidates_token_count" in usage:
        thoughts = _count(usage.get("thoughts_token_count"))
        if outputs is not None and thoughts is not None:
            outputs = _count(outputs + thoughts)
    details = usage.get('input_token_details')
    cached = _count(details.get('cache_read')) if isinstance(details,dict) else None
    if cached is None:
        cached = _count(usage.get('cached_content_token_count'))
    if cached is not None and (inputs is None or cached > inputs):
        cached = None
    provider = provider or provider_for(None,model)
    cost = None
    if inputs is not None and outputs is not None and input_rate is not None and output_rate is not None:
        cost = math.ceil(inputs * input_rate + outputs * output_rate)
    store.execute("""INSERT INTO usage_calls(task_id,role,kind,provider,model,elapsed_ms,input_tokens,output_tokens,total_tokens,estimated_micro_usd,extra_cost_unknown,status,at,cached_input_tokens)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (task, role, kind, provider, model if isinstance(model,str) and len(model)<200 else None,
         max(0, round((time.monotonic()-started)*1000)),inputs,outputs,total,cost,int(extra_unknown),
         "failed" if error else "success",time.time(),cached))

def calls(store, task):
    return store.execute("SELECT role,kind,provider,model,elapsed_ms,input_tokens,output_tokens,total_tokens,estimated_micro_usd,extra_cost_unknown,status,at,cached_input_tokens FROM usage_calls WHERE task_id=%s ORDER BY id", (task,))


def summary(rows):
    return {
        "logical_calls":len(rows),
        "known_estimated_micro_usd":sum(row["estimated_micro_usd"] or 0 for row in rows),
        "cost_unknown_calls":sum(row["estimated_micro_usd"] is None or row["extra_cost_unknown"] for row in rows),
        "note":"已知费用仅为估算；未知费用、SDK 内部重试和供应商账单可能不同。",
    }

def price_for(model, price_model, input_rate, output_rate, *, provider=None, price_provider=None):
    if price_provider is not None and provider != price_provider:
        return None, None
    if model and model == price_model and input_rate is not None and output_rate is not None:
        return input_rate, output_rate
    return None, None
