from app.config import DEFAULT_ORDER_KG
from app.plan import build_plan, lot_table, summary_rows
from app.errors import DomainError
from app.numeric import number
def apply(ctx):
    order=number(ctx.get('order_kg') if ctx.get('order_kg') not in (None,'') else DEFAULT_ORDER_KG)
    if order<=0: raise DomainError('RANGE','订单吨数必须大于零')
    lots=lot_table(ctx['eligible'])
    plan=build_plan(lots,order,ctx.get('batch'))
    ctx['plan']=plan
    rows=summary_rows(plan)
    metrics=[['订单_kg',plan['order_kg'],'kg'],['炉次',plan['batch'] or '—',''],['可行方案',len(rows),'项'],['搜索',('精确' if plan['exact'] else '截断'),'']]
    return {'rows':rows,'metrics':metrics,'plan':plan,'options':plan['options'],'excluded':plan['excluded'],'notes':plan['notes'],'frontier':plan['frontier']}
