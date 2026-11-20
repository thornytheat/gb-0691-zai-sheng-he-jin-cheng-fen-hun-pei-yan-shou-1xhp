import sqlite3, json, os, time
from pathlib import Path
from app.errors import DomainError
def connection():
    path=Path(os.environ.get('APP_DB','data/runs.sqlite3'));path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path);db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, payload TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核")')
    db.execute('CREATE TABLE IF NOT EXISTS requisitions(order_id TEXT PRIMARY KEY, payload TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核")')
    return db
def save(result):
    with connection() as db:
        ident=result['manifest']['run_id'];row=db.execute('SELECT payload,revision,decision FROM runs WHERE id=?',(ident,)).fetchone()
        if row: return {**json.loads(row['payload']), 'revision':row['revision'], 'decision':row['decision'], 'reused':True}
        db.execute('INSERT INTO runs(id,payload) VALUES (?,?)',(ident,json.dumps(result,ensure_ascii=False)))
    return {**result,'revision':1,'decision':'待复核','reused':False}
def get_run(ident):
    with connection() as db: row=db.execute('SELECT payload,revision,decision FROM runs WHERE id=?',(ident,)).fetchone()
    if row is None: raise DomainError('NOT_FOUND','批次不存在',404)
    return {**json.loads(row['payload']),'revision':row['revision'],'decision':row['decision']}
def latest():
    with connection() as db: row=db.execute('SELECT payload,revision,decision FROM runs ORDER BY rowid DESC LIMIT 1').fetchone()
    return {**json.loads(row['payload']),'revision':row['revision'],'decision':row['decision']} if row else None
def history():
    with connection() as db: return [dict(r) for r in db.execute('SELECT id,revision,decision FROM runs ORDER BY rowid DESC')]
def review(ident, revision, decision):
    if decision not in ('通过','退回'): raise DomainError('DECISION','复核动作无效')
    with connection() as db:
        changed=db.execute('UPDATE runs SET revision=revision+1,decision=? WHERE id=? AND revision=?',(decision,ident,revision)).rowcount
        if not changed: raise DomainError('VERSION','版本已变化，请重新载入',409)
    return {'revision':revision+1,'decision':decision}

# 领料记录：一个订单只有一条，方案重算后再次确认只更新这一条，不新增第二条。
def requisition(order_id):
    with connection() as db: row=db.execute('SELECT payload,revision,decision FROM requisitions WHERE order_id=?',(order_id,)).fetchone()
    return {**json.loads(row['payload']),'revision':row['revision'],'decision':row['decision']} if row else None
def confirm_requisition(record):
    """按订单幂等确认：内容一致直接复用；内容变化原地更新并递增版本。"""
    order_id=record['order_id']
    with connection() as db:
        row=db.execute('SELECT payload,revision FROM requisitions WHERE order_id=?',(order_id,)).fetchone()
        if row is None:
            db.execute('INSERT INTO requisitions(order_id,payload,decision) VALUES (?,?,?)',(order_id,json.dumps(record,ensure_ascii=False),'已确认'))
            return {**record,'revision':1,'decision':'已确认','reused':False}
        current=json.loads(row['payload'])
        fingerprint=('option_key','rows','总成本_元','超出订单_kg','领料总量_kg')
        if all(current.get(k)==record.get(k) for k in fingerprint):
            return {**current,'revision':row['revision'],'decision':'已确认','reused':True}
        record['superseded_run_id']=current.get('run_id')
        record['confirmed_at']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
        db.execute('UPDATE requisitions SET payload=?,revision=revision+1,decision="已确认" WHERE order_id=?',(json.dumps(record,ensure_ascii=False),order_id))
        return {**record,'revision':row['revision']+1,'decision':'已确认','reused':False}
def requisition_history():
    with connection() as db: return [dict(r) for r in db.execute('SELECT order_id,revision,decision FROM requisitions ORDER BY rowid DESC')]
