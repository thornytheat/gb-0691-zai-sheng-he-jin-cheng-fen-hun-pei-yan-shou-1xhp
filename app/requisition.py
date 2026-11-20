import time

from app.errors import DomainError
from app.plan import examine, option_by_key, plan_acceptance
from app.store import confirm_requisition, get_run, requisition

FIELDS = ('run_id', 'option_key', 'rows', '总成本_元', '超出订单_kg')


def confirm(run_id, option_key):
    run = get_run(run_id)
    plan = run.get('plan') or {}
    if not plan.get('feasible'):
        raise DomainError('PLAN', '当前批次没有可确认的领料方案')
    option = option_by_key(plan, option_key)
    record = {
        'order_id': plan['order_id'],
        'order_kg': plan['order_kg'],
        'run_id': run_id,
        'sha256': run['manifest']['sha256'],
        'option_key': option_key,
        'option_label': option['label'],
        'cost_source': plan.get('batch'),
        'rows': option['rows'],
        '总成本_元': option['总成本_元'],
        '超出订单_kg': option['超出订单_kg'],
        '领料总量_kg': option['领料总量_kg'],
        'Cu含量下界_pct': option['Cu含量下界_pct'],
        'Cu含量上界_pct': option['Cu含量上界_pct'],
        'Zn含量下界_pct': option['Zn含量下界_pct'],
        'Zn含量上界_pct': option['Zn含量上界_pct'],
        '验收': option['验收'],
        'confirmed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    return confirm_requisition(record)


def current(order_id, run=None):
    """读回确认记录，并对当前批次重新体检（晚到化验记录会让方案失配）。"""
    record = requisition(order_id)
    if record is None:
        return None
    if run is None:
        return record
    return {**record, 'check': examine(record, run)}


def acceptance(run, record):
    """验收按已确认的领料用量重算。"""
    result = plan_acceptance(record, run)
    label = f"{record['option_label']} · r{record['revision']}"
    return {**result, 'label': label, 'option_key': record['option_key']}
