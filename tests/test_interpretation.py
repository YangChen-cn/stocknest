from copy import deepcopy
from datetime import date
import pytest
from stockwatch.interpretation import interpret_instrument, interpret_concentration, compact_badges


def facts(**values):
    defaults = dict(sma20='110', sma50='100', distance_sma20_pct='9', distance_sma50_pct='20', rsi14='60',
                    macd_signal_difference='2', atr_price_ratio='.04', atr14='4', historical_volatility20_pct='20')
    defaults.update(values)
    return dict(session='2026-10-06', expected_session='2026-10-06', previous_session='2026-10-05', source='test',
        quality='complete', adjustment='split_adjusted_no_dividends', close_usd='120',
        metrics={key: dict(value=value, valid_samples=60, quality='complete', session='2026-10-06', reason=None) for key,value in defaults.items()},
        changes_from_previous_session={key: dict(previous_value=str(float(value)-1), delta='1', reason=None) for key,value in defaults.items()})


@pytest.mark.parametrize('value,stage', [(0,'oversold'),(29.99,'oversold'),(30,'weak'),(44.99,'weak'),(45,'neutral'),(55,'neutral'),(55.01,'strong'),(70,'strong'),(70.01,'overbought'),(100,'overbought'),(101,'unavailable')])
def test_rsi_boundaries(value,stage):
    assert interpret_instrument(facts(rsi14=str(value)))['rsi']['stage_id'] == stage


@pytest.mark.parametrize('current,old,stage', [(2,1,'positive_strengthening'),(1,2,'positive_weakening'),(-2,-1,'negative_strengthening'),(-1,-2,'negative_weakening'),(0,1,'zero_steady'),(1,1,'positive_steady')])
def test_macd_states(current,old,stage):
    item=facts(macd_signal_difference=str(current))
    item['changes_from_previous_session']['macd_signal_difference'].update(previous_value=str(old),delta=str(current-old))
    assert interpret_instrument(item)['macd']['stage_id']==stage


@pytest.mark.parametrize('patch', [dict(quality='stale'),dict(adjustment='unknown'),dict(session='2026-10-05')])
def test_bad_data_not_inferred(patch):
    item=facts();item.update(patch)
    assert all(stage['stage_id']=='unavailable' for stage in interpret_instrument(item).values())
    assert not compact_badges(interpret_instrument(item))


def test_missing_comparison_not_fabricated_and_units_and_immutability():
    item=facts();item['changes_from_previous_session']={}
    original=deepcopy(item)
    nodes=interpret_instrument(item,'zh-CN')
    assert nodes['macd']['stage_id']=='positive_unknown'
    assert nodes['atr']['stage_id']=='unknown'
    assert any('4.0%' in badge['label'] for badge in compact_badges(nodes,'zh-CN'))
    assert item==original
    assert interpret_instrument({})['trend']['stage_id']=='unavailable'
    item['metrics']['sma20']['value']=None
    assert interpret_instrument(item)['trend']['stage_id']=='unavailable'


def test_trend_mixed_weak_and_exposure_count():
    item=facts(); item['close_usd']='90';item['metrics']['sma20']['value']='95'
    assert interpret_instrument(item)['trend']['stage_id']=='weak'
    item['close_usd']='95'
    assert interpret_instrument(item)['trend']['stage_id']=='mixed'
    exposure=interpret_concentration(dict(status='available',hhi='.25'))
    assert exposure['values']['effective_equal_weight_count']=='4'
    assert interpret_concentration({})['stage_id']=='unavailable'


def test_missing_metric_quality_and_nonadjacent_changes_do_not_make_strength_claims():
    item=facts(); item['previous_session']='2026-10-02'
    nodes=interpret_instrument(item)
    assert nodes['macd']['stage_id']=='positive_unknown'
    assert nodes['atr']['stage_id']=='unknown'
    item['metrics']['rsi14']['quality']='partial'
    assert interpret_instrument(item)['rsi']['stage_id']=='unavailable'


def test_badge_html_escapes_and_stale_is_omitted():
    from stockwatch.presentation import email_quant_badges
    html=email_quant_badges([dict(tone='gray',kind='rsi',session='2026-10-06',detail='<script>',label='<img>')])
    assert '<script>' not in html and '&lt;img&gt;' in html


def test_claimed_complete_with_insufficient_samples_is_unknown():
    item=facts();item['metrics']['sma20']['valid_samples']=19
    assert interpret_instrument(item)['trend']['stage_id']=='unavailable'
    item['metrics']['atr_price_ratio']['value']='-.04'
    assert interpret_instrument(item)['atr']['stage_id']=='unavailable'
