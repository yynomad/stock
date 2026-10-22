"""Atomic estimated-cost reservations. Unknown provider usage remains charged, never zeroed."""
import math
import uuid

class BudgetExceeded(RuntimeError):pass

class Budget:
    def __init__(self,store,config):
        self.store=store;self.config=config
        store.execute('CREATE TABLE IF NOT EXISTS brief_budget(id TEXT PRIMARY KEY,day TEXT,reserved INTEGER,charge INTEGER,state TEXT,input_tokens INTEGER,output_tokens INTEGER)')
    def cost(self,inputs,outputs):
        # micro-USD: USD/token * 1e6 cancels the per-million rate.
        return math.ceil(inputs*self.config.input_rate+outputs*self.config.output_rate)
    def reserve(self,day,input_upper):
        amount=self.cost(input_upper,self.config.max_output)
        with self.store.lock:
            self.store.conn.execute('BEGIN IMMEDIATE')
            try:
                row=self.store.conn.execute('SELECT COALESCE(SUM(charge),0),COUNT(*) FROM brief_budget WHERE day=?',(day,)).fetchone()
                if self.config.budget==0 or row[0]+amount>math.floor(self.config.budget*1000000) or row[1]>=self.config.max_calls:raise BudgetExceeded('daily model budget/call cap reached')
                identity=uuid.uuid4().hex
                self.store.conn.execute('INSERT INTO brief_budget VALUES(?,?,?,?,?,?,?)',(identity,day,amount,amount,'reserved',None,None))
                self.store.conn.commit();return identity
            except BaseException:self.store.conn.rollback();raise
    def settle(self,identity,usage=None):
        valid=isinstance(usage,dict) and all(type(usage.get(k)) is int and usage[k]>=0 for k in ('input_tokens','output_tokens'))
        if valid:
            cost=self.cost(usage['input_tokens'],usage['output_tokens'])
            self.store.execute("UPDATE brief_budget SET charge=?,state='usage_reported',input_tokens=?,output_tokens=? WHERE id=? AND state='reserved'",(cost,usage['input_tokens'],usage['output_tokens'],identity))
        else:self.store.execute("UPDATE brief_budget SET state='usage_unknown' WHERE id=? AND state='reserved'",(identity,))
    def status(self,day):
        rows=self.store.execute('SELECT state,charge FROM brief_budget WHERE day=?',(day,))
        return {'estimated_usd':sum(r['charge'] for r in rows)/1000000,'calls':len(rows),'usage_unknown':sum(r['state']!='usage_reported' for r in rows),'limit_usd':self.config.budget,'note':'应用估算，含未知调用预留；不是供应商账单硬封顶'}
