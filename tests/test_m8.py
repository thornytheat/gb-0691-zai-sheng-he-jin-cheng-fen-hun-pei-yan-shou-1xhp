def test_plan_sample_feasible(ctx):
    plan=ctx['plan']
    assert plan['feasible'] and plan['batch']=='F-1108' and plan['exact'] is True

def test_plan_three_options_bracket_tradeoff(ctx):
    options={o['key']:o for o in ctx['plan']['options']}
    assert set(options)=={'cheap','balanced','tight'}
    from app.numeric import number
    cheap,balanced,tight=options['cheap'],options['balanced'],options['tight']
    # 少花钱 vs 少剩料：成本递增、超量递减
    assert number(cheap['总成本_元'])<number(balanced['总成本_元'])<number(tight['总成本_元'])
    assert number(cheap['超出订单_kg'])>number(balanced['超出订单_kg'])>number(tight['超出订单_kg'])

def test_plan_options_meet_order_and_targets(ctx):
    from decimal import Decimal
    from app.numeric import number
    for option in ctx['plan']['options']:
        assert number(option['领料总量_kg'])>=Decimal('2400')
        assert option['验收']=='通过'

def test_plan_whole_lots_only(ctx):
    from app.numeric import number
    from decimal import Decimal
    wet={r['lot']:number(r['wet_mass']) for r in ctx['eligible']}
    for option in ctx['plan']['options']:
        for row in option['rows']:
            assert number(row['领料重量_kg'])==wet[row['批号']]
        assert number(option['领料总量_kg'])==sum((number(r['领料重量_kg']) for r in option['rows']),Decimal(0))

def test_plan_cost_is_sum_of_rows(ctx):
    from decimal import Decimal
    from app.numeric import number
    for option in ctx['plan']['options']:
        assert number(option['总成本_元'])==sum((number(r['金额_元']) for r in option['rows']),Decimal(0)).quantize(Decimal('0.01'))

def test_plan_excludes_incomplete_and_unpriced_lots(ctx):
    excluded={row['批号']:row['原因'] for row in ctx['plan']['excluded']}
    assert '缺测' in excluded['00902'] and '无法保证上下限' in excluded['00902']
    assert '缺测' in excluded['00903'] and '无法保证上下限' in excluded['00903']

def test_plan_summary_shows_gap_to_cheapest(ctx):
    cheap=next(r for r in ctx['stages']['8']['rows'] if r['方案']=='最低成本')
    tight=next(r for r in ctx['stages']['8']['rows'] if r['方案']=='最少超量')
    assert cheap['较最低成本_元']=='0.00' and cheap['少剩料_kg']=='0.000'
    assert tight['较最低成本_元']!='0.00' and tight['少剩料_kg']!='0.000'

def test_plan_order_is_parameterised(raw):
    import csv,io
    from app.pipeline import calculate
    rows=list(csv.DictReader(io.StringIO(raw)))
    out=io.StringIO();writer=csv.DictWriter(out,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    small=calculate(order_kg='1000')
    big=calculate(order_kg='2500')
    assert small['plan']['order_kg']=='1000' and big['plan']['order_kg']=='2500'
    assert small['plan']['feasible'] and big['plan']['feasible']
    assert small['plan']['order_id']!=big['plan']['order_id']

def test_plan_infeasible_when_order_exceeds_supply(ctx):
    from app.pipeline import calculate
    run=calculate(order_kg='5000')
    assert run['plan']['feasible'] is False and run['plan']['options']==[]

def test_plan_unknown_batch_is_infeasible(ctx):
    from app.pipeline import calculate
    assert calculate(batch='F-NONE')['plan']['feasible'] is False

def test_plan_acceptance_uses_confirmed_usage(raw):
    from app.pipeline import calculate
    from app.plan import plan_acceptance
    run=calculate()
    option=next(o for o in run['plan']['options'] if o['key']=='tight')
    record={'rows':option['rows'],'option_key':'tight','option_label':'最少超量'}
    result=plan_acceptance(record,run)
    from app.numeric import number
    assert number(result['dry_total_kg'])<number({'cheap':'2450','balanced':'2410.8','tight':'2371.6'}['tight'])+1
    assert result['passed'] is True
    assert len(result['usage'])==len(option['rows'])

def test_plan_acceptance_survives_missing_assay(raw):
    from app.pipeline import calculate
    from app.plan import plan_acceptance
    run=calculate()
    option=next(o for o in run['plan']['options'] if o['key']=='cheap')
    record={'rows':option['rows'],'option_key':'cheap','option_label':'最低成本'}
    result=plan_acceptance(record,run)
    assert result['passed'] is True
