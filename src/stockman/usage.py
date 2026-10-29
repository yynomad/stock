"""Local, content-free accounting of logical provider calls; not provider invoices."""
import math
import time

def _count(value):
    return value if type(value) is int and value >= 0 else None

def record(store, task, role, kind, model, started, usage=None, error=None, input_rate=None, output_rate=None, extra_unknown=False):
    usage = usage if isinstance(usage, dict) else {}
    inputs = _count(usage.get("input_tokens", usage.get("prompt_token_count")))
    outputs = _count(usage.get("output_tokens", usage.get("candidates_token_count")))
    total = _count(usage.get("total_tokens", usage.get("total_token_count")))
    if "candidates_token_count" in usage:
        thoughts = _count(usage.get("thoughts_token_count"))
        if outputs is not None and thoughts is not None:
            outputs += thoughts
    cost = None
    if inputs is not None and outputs is not None and input_rate is not None and output_rate is not None:
        cost = math.ceil(inputs * input_rate + outputs * output_rate)
    store.execute("""INSERT INTO usage_calls(task_id,role,kind,provider,model,elapsed_ms,input_tokens,output_tokens,total_tokens,estimated_micro_usd,extra_cost_unknown,status,at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (task, role, kind, "google" if isinstance(model,str) and model.startswith("gemini") else None,
         model if isinstance(model,str) and len(model)<200 else None, max(0, round((time.monotonic()-started)*1000)),
         inputs,outputs,total,cost,int(extra_unknown),"failed" if error else "success",time.time()))

def calls(store, task):
    return store.execute("SELECT role,kind,provider,model,elapsed_ms,input_tokens,output_tokens,total_tokens,estimated_micro_usd,extra_cost_unknown,status,at FROM usage_calls WHERE task_id=? ORDER BY id", (task,))
