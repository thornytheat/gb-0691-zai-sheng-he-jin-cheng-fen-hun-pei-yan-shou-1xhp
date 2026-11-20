from flask import Blueprint, render_template, request
from app.config import TITLE, MODULES, SHORT, SAMPLE, DEFAULT_ORDER_KG
from app.pipeline import calculate
from app.store import latest, save, get_run, requisition
from app.requisition import acceptance, current
bp=Blueprint('web',__name__)

def _run():
    run=get_run(request.args['run_id']) if request.args.get('run_id') else latest()
    if run is None: run=save(calculate())
    return run

def _requisition(run):
    plan=run.get('plan') or {}
    if not plan.get('order_id'): return None
    record=requisition(plan['order_id'])
    if record is None: return None
    record={**record,'check':current(plan['order_id'],run)['check']}
    if record['check']['status']=='invalid':
        record['acceptance']={'rows':[],'usage':[],'metrics':[],
                              'notes':['当前数据已无法重建原方案，验收须重新选择领料方案。'],'passed':False}
    else:
        record['acceptance']=acceptance(run,record)
    return record

@bp.route('/')
@bp.route('/work/<int:stage>')
def work(stage=1):
    if stage<1 or stage>len(MODULES):
        from flask import abort
        abort(404)
    run=_run()
    if str(stage) not in run['stages']:
        from flask import abort
        abort(404)
    current_stage=run['stages'][str(stage)]
    plan=run.get('plan') or {}
    return render_template('m'+str(stage)+'.html', title=TITLE, modules=MODULES, short=SHORT, run=run,
                           current=current_stage, stage=stage, plan=plan, requisition=_requisition(run),
                           default_order=DEFAULT_ORDER_KG,
                           sample=SAMPLE.read_text(encoding='utf-8-sig'))
