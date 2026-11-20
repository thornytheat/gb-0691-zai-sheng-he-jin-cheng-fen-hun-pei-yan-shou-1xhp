"""整批领料方案：在“少花钱”和“少剩料”之间给出可选方案并锁定用量。

规则：
- 整批发料，不能拆包；一个方案只能是若干整批的组合。
- 订单量按领料称重（湿重）比较，元素含量按干基汇总。
- 只有同时测得 Cu/Zn 且带有效单价的整批料才能进方案；缺测或没价的整批料
  列在 excluded 里，不能靠猜。
- 枚举所有“达到订单即停”的组合，成本与超量都更差的组合不会漏掉最优解。
"""
from collections import defaultdict
from decimal import Decimal

from app.domain import TARGETS, blend, mass_kg, unit_price, verdict
from app.errors import DomainError
from app.numeric import fmt, number

NODE_CAP = 3_000_000
FRONTIER_CAP = 12
MASS_SCALE = 10 ** 6
PRICE_SCALE = 10 ** 4


def order_id_for(order_kg):
    value = number(order_kg).normalize()
    if value == value.to_integral_value():
        value = value.to_integral_value()
    return 'ORDER-' + format(value, 'f')


def lot_table(eligible):
    """把可配成分记录折叠为整批：一批一行，含湿重、干重、单价与 Cu/Zn 含量。"""
    lots = {}
    for row in eligible:
        key = (row['batch'], row['lot'])
        lot = lots.get(key)
        if lot is None:
            wet = row.get('wet_mass')
            wet = number(wet) if wet not in (None, '') else mass_kg(row['mass'], row['unit'])
            lot = lots[key] = {
                'batch': row['batch'], 'lot': row['lot'],
                'wet': wet, 'dry': number(row['dry_mass']),
                'moisture': number(row['moisture']),
                'price_raw': (row.get('price') or '').strip(),
                'elements': {}, 'dry_conflict': False,
            }
        elif number(row['dry_mass']) != lot['dry']:
            lot['dry_conflict'] = True
        lot['elements'][row['element']] = (number(row['low']), number(row['high']))
    for lot in lots.values():
        try:
            lot['price'] = unit_price(lot['price_raw'])
            lot['price_state'] = 'ok' if lot['price'] is not None else 'missing'
        except DomainError:
            lot['price'] = None
            lot['price_state'] = 'invalid'
    return lots


def choose_batch(lots):
    """默认炉次：可整批领用（Cu/Zn 齐全且有单价）总量最大的炉次。"""
    totals = defaultdict(Decimal)
    for lot in lots.values():
        if _usable(lot):
            totals[lot['batch']] += lot['wet']
    if not totals:
        return None
    return sorted(totals, key=lambda b: (-totals[b], b))[0]


def _usable(lot):
    return len(lot['elements']) == 2 and lot['price'] is not None and not lot['dry_conflict']


def _scaled(lot):
    wet = int((lot['wet'] * MASS_SCALE).to_integral_value())
    dry = int((lot['dry'] * MASS_SCALE).to_integral_value())
    price = int((lot['price'] * PRICE_SCALE).to_integral_value())
    cu_low, cu_high = lot['elements']['Cu']
    zn_low, zn_high = lot['elements']['Zn']
    return {
        'batch': lot['batch'], 'lot': lot['lot'], 'wet': wet, 'dry': dry, 'price': price,
        'cu_low': int((cu_low * MASS_SCALE).to_integral_value()),
        'cu_high': int((cu_high * MASS_SCALE).to_integral_value()),
        'zn_low': int((zn_low * MASS_SCALE).to_integral_value()),
        'zn_high': int((zn_high * MASS_SCALE).to_integral_value()),
    }


def _targets():
    return tuple(int((TARGETS[e][i] * MASS_SCALE).to_integral_value()) for e in ('Cu', 'Zn') for i in (0, 1))


def _search(candidates, order):
    """枚举所有达到订单即停的整批组合；只保留各超量档的最小成本，避免内存膨胀。"""
    n = len(candidates)
    order = int((order * MASS_SCALE).to_integral_value())
    suffix_wet = [0] * (n + 1)
    suffix_dry = [0] * (n + 1)
    suffix = {key: [0] * (n + 1) for key in ('cu_low', 'cu_high', 'zn_low', 'zn_high')}
    for i in range(n - 1, -1, -1):
        c = candidates[i]
        suffix_wet[i] = suffix_wet[i + 1] + c['wet']
        suffix_dry[i] = suffix_dry[i + 1] + c['dry']
        for key in suffix:
            suffix[key][i] = suffix[key][i + 1] + c['dry'] * c[key]
    cu_lo, cu_hi, zn_lo, zn_hi = _targets()
    best_cost = [None]
    best_tight = [None]
    by_excess = {}
    state = {'nodes': 0, 'plans': 0, 'truncated': False}

    def record(tot, dry, cost, cu_l, cu_h, zn_l, zn_h, chosen):
        state['plans'] += 1
        excess = tot - order
        cost_key = (cost, tot, chosen)
        if best_cost[0] is None or cost_key < best_cost[0]:
            best_cost[0] = cost_key
        tight_key = (tot, cost, chosen)
        if best_tight[0] is None or tight_key < best_tight[0]:
            best_tight[0] = tight_key
        previous = by_excess.get(excess)
        if previous is None or (cost, tot, chosen) < previous:
            by_excess[excess] = (cost, tot, chosen)

    def walk(i, tot, dry, cost, cu_l, cu_h, zn_l, zn_h, chosen):
        state['nodes'] += 1
        if state['nodes'] > NODE_CAP:
            state['truncated'] = True
            return
        if tot + suffix_wet[i] < order:
            return
        if tot >= order:
            record(tot, dry, cost, cu_l, cu_h, zn_l, zn_h, tuple(chosen))
            return
        if i >= n:
            return
        dry_left = dry + suffix_dry[i]
        if cu_l + suffix['cu_low'][i] < cu_lo * dry:
            return
        if cu_h > cu_hi * dry_left:
            return
        if zn_l + suffix['zn_low'][i] < zn_lo * dry:
            return
        if zn_h > zn_hi * dry_left:
            return
        c = candidates[i]
        chosen.append(i)
        walk(i + 1, tot + c['wet'], dry + c['dry'], cost + c['wet'] * c['price'] // PRICE_SCALE,
             cu_l + c['dry'] * c['cu_low'], cu_h + c['dry'] * c['cu_high'],
             zn_l + c['dry'] * c['zn_low'], zn_h + c['dry'] * c['zn_high'], chosen)
        chosen.pop()
        walk(i + 1, tot, dry, cost, cu_l, cu_h, zn_l, zn_h, chosen)

    walk(0, 0, 0, 0, 0, 0, 0, 0, [])
    return order, best_cost[0], best_tight[0], by_excess, state


def _percent(value, dry):
    """元素质量比 -> 质量分数（0–1）。质量与含量都放大了 MASS_SCALE 倍。"""
    return Decimal(value) / (Decimal(dry) * 100 * MASS_SCALE) if dry else Decimal(0)


def _plan_row(candidates, chosen, order, total, dry, cost, cu_l, cu_h, zn_l, zn_h):
    cu_low, cu_high = _percent(cu_l, dry), _percent(cu_h, dry)
    zn_low, zn_high = _percent(zn_l, dry), _percent(zn_h, dry)
    return {
        'chosen': chosen,
        'total': Decimal(total) / MASS_SCALE,
        'dry': Decimal(dry) / MASS_SCALE,
        'cost': Decimal(cost) / MASS_SCALE,
        'excess': (Decimal(total) - order) / MASS_SCALE,
        'Cu': (cu_low, cu_high), 'Zn': (zn_low, zn_high),
        'rows': _rows_for(chosen, candidates),
    }


def _rows_for(chosen, candidates):
    rows = []
    for index in sorted(chosen, key=lambda i: candidates[i]['lot']):
        c = candidates[index]
        wet = Decimal(c['wet']) / MASS_SCALE
        price = Decimal(c['price']) / PRICE_SCALE
        cu_low, cu_high = Decimal(c['cu_low']) / MASS_SCALE, Decimal(c['cu_high']) / MASS_SCALE
        zn_low, zn_high = Decimal(c['zn_low']) / MASS_SCALE, Decimal(c['zn_high']) / MASS_SCALE
        rows.append({
            '炉次': c['batch'],
            '批号': c['lot'],
            '领料重量_kg': fmt(wet, '0.001'),
            '干重_kg': fmt(Decimal(c['dry']) / MASS_SCALE, '0.001'),
            '单价_元每kg': fmt(price, '0.0001'),
            '金额_元': fmt((wet * price).quantize(Decimal('0.01')), '0.01'),
            'Cu含量_pct': fmt(cu_low, '0.0001') if cu_low == cu_high else f'{fmt(cu_low)}–{fmt(cu_high)}',
            'Zn含量_pct': fmt(zn_low, '0.0001') if zn_low == zn_high else f'{fmt(zn_low)}–{fmt(zn_high)}',
        })
    return rows


def _option(key, label, plan):
    cu_low, cu_high = plan['Cu']
    zn_low, zn_high = plan['Zn']
    return {
        'key': key,
        'label': label,
        '批数': len(plan['chosen']),
        '领料总量_kg': fmt(plan['total'], '0.001'),
        '干重_kg': fmt(plan['dry'], '0.001'),
        '超出订单_kg': fmt(plan['excess'], '0.001'),
        '总成本_元': fmt(plan['cost'], '0.01'),
        'Cu含量下界_pct': fmt(cu_low * 100),
        'Cu含量上界_pct': fmt(cu_high * 100),
        'Zn含量下界_pct': fmt(zn_low * 100),
        'Zn含量上界_pct': fmt(zn_high * 100),
        '验收': '通过' if verdict('Cu', cu_low * 100, cu_high * 100) == '通过'
                and verdict('Zn', zn_low * 100, zn_high * 100) == '通过' else '不合格',
        'rows': plan['rows'],
    }


def _public(option):
    return {k: v for k, v in option.items() if k != '_plan'}


def _knee(points):
    """成本-超量折中：归一化后离“两端连线”最远的点，即牺牲最少成本换最多剩料改善。"""
    if len(points) <= 2:
        return 0
    e_min, e_max = points[0]['excess'], points[-1]['excess']
    c_tight, c_cheap = points[0]['cost'], points[-1]['cost']
    if e_max == e_min or c_tight == c_cheap:
        return 0
    best_index, best_score = 0, None
    for index, point in enumerate(points):
        x = float((point['excess'] - e_min) / (e_max - e_min))
        y = float((point['cost'] - c_cheap) / (c_tight - c_cheap))
        # 直线 x + y = 1 经过两端，值越小离直线越远；同分时取成本更低的点
        score = -(1 - (x + y))
        key = (score, float(point['cost']))
        if best_score is None or key < best_score:
            best_index, best_score = index, key
    return best_index


def _evaluate(candidates, chosen, scaled_order):
    """由选中的批号下标重建一份候选方案（含明细行与 Cu/Zn 区间）。"""
    dry = sum(candidates[i]['dry'] for i in chosen)
    total = sum(candidates[i]['wet'] for i in chosen)
    cost = sum(candidates[i]['wet'] * candidates[i]['price'] // PRICE_SCALE for i in chosen)
    cu_l = sum(candidates[i]['dry'] * candidates[i]['cu_low'] for i in chosen)
    cu_h = sum(candidates[i]['dry'] * candidates[i]['cu_high'] for i in chosen)
    zn_l = sum(candidates[i]['dry'] * candidates[i]['zn_low'] for i in chosen)
    zn_h = sum(candidates[i]['dry'] * candidates[i]['zn_high'] for i in chosen)
    return _plan_row(candidates, tuple(chosen), scaled_order, total, dry, cost, cu_l, cu_h, zn_l, zn_h)


def build_plan(lots, order_kg, batch=None):
    order = number(order_kg)
    batch = batch or choose_batch(lots)
    base = {'order_kg': str(order), 'order_id': order_id_for(order), 'batch': batch}
    if batch is None:
        return {**base, 'feasible': False, 'exact': True, 'options': [], 'frontier': [],
                'excluded': [], 'notes': ['没有同时测得 Cu/Zn 且带有效单价的整批来料，无法生成领料方案。'],
                'candidate_lots': 0}
    candidates, excluded = [], []
    for lot in lots.values():
        if lot['batch'] != batch:
            continue
        if lot['dry_conflict']:
            excluded.append({'批号': lot['lot'], '原因': '批内干重冲突'})
        elif len(lot['elements']) != 2:
            missing = [e for e in ('Cu', 'Zn') if e not in lot['elements']]
            excluded.append({'批号': lot['lot'], '原因': f'缺测 {"、".join(missing)}，无法保证上下限'})
        elif lot['price'] is None:
            excluded.append({'批号': lot['lot'],
                             '原因': '单价格式无效' if lot['price_state'] == 'invalid' else '缺单价'})
        else:
            candidates.append(_scaled(lot))
    candidates.sort(key=lambda c: (c['price'], c['lot']))
    excluded.sort(key=lambda e: e['批号'])
    available = sum((Decimal(c['wet']) / MASS_SCALE for c in candidates), Decimal(0))
    notes = []
    if not candidates:
        return {**base, 'feasible': False, 'exact': True, 'options': [], 'frontier': [],
                'excluded': excluded, 'notes': ['没有可整批领用的料批。'], 'candidate_lots': 0}
    if available < order:
        notes.append(f'可整批领用总量 {fmt(available, "0.001")} kg 不足订单 {fmt(order, "0.001")} kg。')
        return {**base, 'feasible': False, 'exact': True, 'options': [], 'frontier': [],
                'excluded': excluded, 'notes': notes, 'candidate_lots': len(candidates)}
    scaled_order, cheapest, tightest, by_excess, state = _search(candidates, order)
    if not by_excess:
        notes.append('所有整批组合都超出 Cu 7.5–12.5% 或 Zn 3–7% 的目标区间，无可行方案。')
        return {**base, 'feasible': False, 'exact': not state['truncated'], 'options': [],
                'frontier': [], 'excluded': excluded, 'notes': notes,
                'candidate_lots': len(candidates), 'search_nodes': state['nodes']}
    # 超量升序的成本下包络：允许更多剩料时成本只会更低，剔除被后一档支配的点
    staircase = []
    lowest = None
    for excess in sorted(by_excess):
        cost, total, chosen = by_excess[excess]
        if lowest is None or cost < lowest:
            staircase.append({'cost': cost, 'total': total, 'chosen': chosen})
            lowest = cost
    points = [_evaluate(candidates, point['chosen'], scaled_order) for point in staircase]
    knee = points[_knee(points)]
    options = [_option('cheap', '最低成本', _evaluate(candidates, cheapest[2], scaled_order)),
               _option('balanced', '成本/剩料折中', knee),
               _option('tight', '最少超量', _evaluate(candidates, tightest[2], scaled_order))]
    frontier = []
    step = max(1, len(points) // FRONTIER_CAP)
    picks = points[::step]
    if points[-1] not in picks:
        picks.append(points[-1])
    for plan in picks:
        option = _public(_option('', '', plan))
        option.pop('rows', None)
        chosen = set(plan['chosen'])
        missing = [c['lot'] for i, c in enumerate(candidates) if i not in chosen]
        frontier.append({**option, '未领批号': '、'.join(missing) if missing else '—',
                         '未领批数': len(missing)})
    if state['truncated']:
        notes.append('方案搜索达到节点上限，可选方案为已找到的最优候选，可能不是全局最优。')
    return {
        **base, 'feasible': True, 'exact': not state['truncated'],
        'options': [_public(o) for o in options],
        'option_rows': {o['key']: o['rows'] for o in options},
        'frontier': frontier, 'excluded': excluded, 'notes': notes,
        'candidate_lots': len(candidates), 'plan_count': state['plans'],
        'search_nodes': state['nodes'],
    }


def option_by_key(plan, key):
    for option in plan.get('options', []):
        if option['key'] == key:
            return option
    raise DomainError('OPTION', '领料方案中没有该选项')


def summary_rows(plan):
    """方案对比表：把成本差与超量差并排给出。"""
    if not plan.get('feasible'):
        return []
    options = {o['key']: o for o in plan['options']}
    cheap = options['cheap']
    rows = []
    for option in plan['options']:
        cost_gap = number(option['总成本_元']) - number(cheap['总成本_元'])
        excess_gap = number(cheap['超出订单_kg']) - number(option['超出订单_kg'])
        rows.append({
            '方案': option['label'],
            '批数': option['批数'],
            '领料总量_kg': option['领料总量_kg'],
            '超出订单_kg': option['超出订单_kg'],
            '总成本_元': option['总成本_元'],
            '较最低成本_元': fmt(cost_gap, '0.01'),
            '少剩料_kg': fmt(excess_gap, '0.001'),
            'Cu_pct': f"{option['Cu含量下界_pct']}–{option['Cu含量上界_pct']}",
            'Zn_pct': f"{option['Zn含量下界_pct']}–{option['Zn含量上界_pct']}",
            '验收': option['验收'],
        })
    return rows


def usage_mixture(usage_rows, eligible):
    """按已确认用量重建混配：数量锁定为领料量，成分取当前批次数据。"""
    by_lot = defaultdict(list)
    for row in eligible:
        by_lot[(row['batch'], row['lot'])].append(row)
    rows, notes, used = [], [], []
    for usage in usage_rows:
        key = (usage['炉次'], usage['批号'])
        records = by_lot.get(key)
        wet = number(usage['领料重量_kg'])
        if not records:
            notes.append(f'批号 {key[1]} 当前无可用成分记录，验收待补测。')
            continue
        current_wet = records[0].get('wet_mass')
        current_wet = number(current_wet) if current_wet not in (None, '') else mass_kg(records[0]['mass'], records[0]['unit'])
        scale = wet / current_wet if current_wet else Decimal(1)
        dry = number(records[0]['dry_mass']) * scale
        present = {record['element']: record for record in records}
        for element in ('Cu', 'Zn'):
            if element in present:
                record = present[element]
                rows.append({'lot': f'{key[0]}/{key[1]}', 'dry_mass': str(dry), 'element': element,
                             'low': record['low'], 'high': record['high']})
        missing = [element for element in ('Cu', 'Zn') if element not in present]
        if missing:
            notes.append(f'批号 {key[1]} 缺测 {"、".join(missing)}，按未知区间计入上界。')
        used.append({'炉次': key[0], '批号': key[1], '领料重量_kg': fmt(wet, '0.001'),
                     '干重_kg': fmt(dry, '0.001'), '缺测元素': '、'.join(missing) or '—'})
    return rows, used, notes


def plan_acceptance(record, run):
    """验收按已确认领料用量重算；数量取确认值，成分取当前批次数据。"""
    rows, used, notes = usage_mixture(record['rows'], run.get('eligible', []))
    mixture = blend(rows)
    if not mixture:
        return {'rows': [], 'usage': used, 'passed': False,
                'notes': notes + ['确认批次的成分记录全部不可用，无法重算验收。'],
                'metrics': [['用量来源', '领料方案', ''], ['可重算批次', 0, '批']],
                'dry_total_kg': '0'}
    result = []
    for row in mixture:
        low, high = number(row['含量下界_pct']), number(row['含量上界_pct'])
        target = TARGETS[row['元素']]
        result.append({**row, '区间宽度_pct': fmt(high - low),
                       '判定': '需补测' if high != low else '确定值',
                       '下限_pct': str(target[0]), '上限_pct': str(target[1]),
                       '验收': verdict(row['元素'], low, high)})
    passed = sum(row['验收'] == '通过' for row in result)
    weights = [number(row['领料重量_kg']) for row in used]
    return {
        'rows': result, 'usage': used, 'notes': notes,
        'metrics': [['用量来源', '领料方案', ''], ['重算批次', len(used), '批'],
                    ['验收通过', passed, '项'], ['领料合计', fmt(sum(weights, Decimal(0)), '0.001'), 'kg']],
        'dry_total_kg': mixture[0]['干重_kg'],
        'passed': passed == len(result) and bool(result),
    }


def examine(record, run):
    """确认后的方案在晚到化验记录下是否仍然成立。"""
    plan = run.get('plan') or {}
    if record['run_id'] == run['manifest']['run_id']:
        return {'status': 'current', 'label': '与当前批次一致', 'changes': []}
    if not plan.get('feasible'):
        return {'status': 'invalid', 'label': '新数据下无可行方案，须重新选择', 'changes': []}
    try:
        option = option_by_key(plan, record['option_key'])
    except DomainError:
        return {'status': 'invalid', 'label': '原方案选项已不可行，须重新选择', 'changes': []}
    old = {row['批号']: number(row['领料重量_kg']) for row in record.get('rows', [])}
    new = {row['批号']: number(row['领料重量_kg']) for row in option['rows']}
    changes = []
    for lot in sorted(set(old) | set(new)):
        before, after = old.get(lot), new.get(lot)
        if before != after:
            changes.append({'批号': lot,
                            '原用量_kg': fmt(before, '0.001') if before is not None else '—',
                            '新用量_kg': fmt(after, '0.001') if after is not None else '—'})
    old_cost = number(record.get('总成本_元', '0'))
    new_cost = number(option['总成本_元'])
    old_excess = number(record.get('超出订单_kg', '0'))
    new_excess = number(option['超出订单_kg'])
    if changes or old_cost != new_cost or old_excess != new_excess:
        return {
            'status': 'stale',
            'label': '化验记录变化，方案已重算，须重新确认',
            'changes': changes,
            '原成本_元': fmt(old_cost, '0.01'), '新成本_元': option['总成本_元'],
            '原超出_kg': record.get('超出订单_kg'), '新超出_kg': option['超出订单_kg'],
        }
    return {'status': 'same', 'label': '新数据未改变本方案', 'changes': []}
