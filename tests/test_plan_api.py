import csv
import io


def _late_assay(raw, element='Cu', assay='6.2'):
    rows = list(csv.DictReader(io.StringIO(raw)))
    late = {**rows[-1], 'element': element, 'assay': assay}
    line = ','.join(late[key] for key in rows[0].keys())
    return raw.rstrip('\n') + '\n' + line + '\n'


def test_confirm_creates_one_record(client):
    run = client.post('/api/run', json={}).get_json()
    response = client.post('/api/requisition', json={'id': run['manifest']['run_id'], 'option': 'balanced'})
    assert response.status_code == 200
    body = response.get_json()
    assert body['revision'] == 1 and body['reused'] is False
    assert body['order_id'] == 'ORDER-2400'
    assert len(body['rows']) == 20
    history = client.get('/api/requisitions').get_json()
    assert len(history) == 1 and history[0]['order_id'] == 'ORDER-2400'


def test_reconfirm_same_plan_is_idempotent(client):
    run = client.post('/api/run', json={}).get_json()
    first = client.post('/api/requisition', json={'id': run['manifest']['run_id'], 'option': 'cheap'}).get_json()
    again = client.post('/api/requisition', json={'id': run['manifest']['run_id'], 'option': 'cheap'}).get_json()
    assert again['revision'] == first['revision'] and again['reused'] is True
    assert len(client.get('/api/requisitions').get_json()) == 1


def test_late_assay_marks_plan_stale(client, raw):
    run = client.post('/api/run', json={'csv': raw}).get_json()
    client.post('/api/requisition', json={'id': run['manifest']['run_id'], 'option': 'balanced'})
    later = client.post('/api/run', json={'csv': _late_assay(raw)}).get_json()
    record = client.get('/api/requisition/ORDER-2400?run_id=' + later['manifest']['run_id']).get_json()
    assert record['check']['status'] == 'stale'
    assert record['check']['changes']
    assert record['check']['新成本_元'] != record['check']['原成本_元']


def test_reconfirm_after_late_assay_updates_single_record(client, raw):
    first = client.post('/api/run', json={'csv': raw}).get_json()
    client.post('/api/requisition', json={'id': first['manifest']['run_id'], 'option': 'balanced'})
    later = client.post('/api/run', json={'csv': _late_assay(raw)}).get_json()
    updated = client.post('/api/requisition', json={'id': later['manifest']['run_id'], 'option': 'balanced'}).get_json()
    assert updated['revision'] == 2 and updated['reused'] is False
    assert updated['run_id'] == later['manifest']['run_id']
    history = client.get('/api/requisitions').get_json()
    assert len(history) == 1 and history[0]['revision'] == 2
    record = client.get('/api/requisition/ORDER-2400?run_id=' + later['manifest']['run_id']).get_json()
    assert record['check']['status'] == 'current'


def test_unrelated_new_batch_keeps_confirmation(client, raw):
    first = client.post('/api/run', json={'csv': raw}).get_json()
    confirmed = client.post('/api/requisition', json={'id': first['manifest']['run_id'], 'option': 'cheap'}).get_json()
    other = client.post('/api/run', json={'csv': raw + '\n'}).get_json()
    record = client.get('/api/requisition/ORDER-2400?run_id=' + other['manifest']['run_id']).get_json()
    assert record['check']['status'] == 'same'
    assert len(client.get('/api/requisitions').get_json()) == 1
    assert confirmed['revision'] == record['revision']


def test_acceptance_recomputed_from_confirmed_usage(client):
    run = client.post('/api/run', json={}).get_json()
    option = next(o for o in run['plan']['options'] if o['key'] == 'tight')
    client.post('/api/requisition', json={'id': run['manifest']['run_id'], 'option': 'tight'})
    page = client.get('/work/6?run_id=' + run['manifest']['run_id']).data.decode()
    assert '按领料用量重算验收' in page and '最少超量' in page
    assert '领料方案' in page and '重算批次' in page


def test_plan_page_shows_quantities_cost_and_acceptance(client):
    run = client.post('/api/run', json={}).get_json()
    page = client.get('/work/8?run_id=' + run['manifest']['run_id']).data.decode()
    assert '可执行领料单' in page and '领料方案对比' in page
    option = run['plan']['options'][0]
    assert option['总成本_元'] in page and option['超出订单_kg'] in page


def test_unknown_plan_option_rejected(client):
    run = client.post('/api/run', json={}).get_json()
    response = client.post('/api/requisition', json={'id': run['manifest']['run_id'], 'option': 'nope'})
    assert response.status_code == 422 and response.get_json()['code'] == 'OPTION'


def test_missing_plan_fields_rejected(client):
    run = client.post('/api/run', json={}).get_json()
    assert client.post('/api/requisition', json={'option': 'cheap'}).status_code == 422
    assert client.post('/api/requisition', json={'id': run['manifest']['run_id']}).status_code == 422


def test_requisition_endpoint_unknown_order_is_404(client):
    assert client.get('/api/requisition/ORDER-1').status_code == 404


def test_plan_without_price_column_is_infeasible(raw):
    header, *body = raw.splitlines()
    without = '\n'.join([','.join(header.split(',')[:-1])] + [','.join(line.split(',')[:-1]) for line in body])
    from app.pipeline import calculate
    result = calculate(without)
    assert result['plan']['feasible'] is False
    assert all(row['原因'] == '缺单价' for row in result['plan']['excluded'])


def test_plan_excluded_lot_without_price(raw):
    from app.pipeline import calculate
    lines = raw.splitlines()
    tuned = [lines[0]] + [line.rsplit(',', 1)[0] + ',' if line.startswith('00002,') else line for line in lines[1:]]
    result = calculate('\n'.join(tuned))
    assert result['plan']['feasible'] is True
    excluded = {row['批号']: row['原因'] for row in result['plan']['excluded']}
    assert excluded['00002'] == '缺单价'
