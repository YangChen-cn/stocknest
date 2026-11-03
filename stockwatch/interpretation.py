"""Deterministic explanations of supplied quantitative facts. No data acquisition."""
from datetime import date
from decimal import Decimal, InvalidOperation

from stockwatch.calendar import previous_session
from stockwatch.i18n import t


def number(value):
    try:
        if isinstance(value, bool) or value is None:
            return None
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def interpret_instrument(item, lang='en'):
    item = item or {}
    metrics = item.get('metrics') or {}
    changes = item.get('changes_from_previous_session') or {}
    day = item.get('session')
    eligible = (day is not None and day == item.get('expected_session') and
                item.get('quality') not in ('stale', 'unavailable') and
                item.get('adjustment') == 'split_adjusted_no_dividends' and item.get('source') not in (None, '', 'unknown'))

    try:
        date.fromisoformat(day)
    except (ValueError, TypeError):
        eligible = False

    def value(name):
        metric = metrics.get(name) or {}
        if not eligible or metric.get('quality') != 'complete' or metric.get('session') != day or metric.get('reason'):
            return None
        required = {'sma20': 20, 'sma50': 50, 'distance_sma20_pct': 20, 'distance_sma50_pct': 50, 'rsi14': 15, 'macd_signal_difference': 34, 'atr14': 14, 'atr_price_ratio': 14, 'historical_volatility20_pct': 21}[name]
        samples = number(metric.get('valid_samples'))
        if samples is None or samples < required:
            return None
        result = number(metric.get('value'))
        if result is not None and (name.startswith('sma') and result <= 0 or name in ('atr14', 'atr_price_ratio', 'historical_volatility20_pct') and result < 0):
            return None
        return result

    def delta(name):
        change = changes.get(name) or {}
        try:
            adjacent = item.get('previous_session') == str(previous_session(date.fromisoformat(day)))
        except (ValueError, TypeError):
            adjacent = False
        old, change_value, current = number(change.get('previous_value')), number(change.get('delta')), value(name)
        if not adjacent or change.get('reason') or None in (old, change_value, current):
            return None
        if abs(current - old - change_value) > Decimal('0.00000001'):
            return None
        return change_value

    def node(stage, label, values, reason, caveat, observe, thresholds=None):
        return dict(stage_id=stage, label_id=label, label=t(label, lang), values={k: str(v) if v is not None else None for k, v in values.items()},
                    reason=t(reason, lang), caveat=t(caveat, lang), next_observation=t(observe, lang),
                    thresholds=thresholds or {}, session=day, expected_session=item.get('expected_session'),
                    source=item.get('source', 'unknown'), quality='unavailable' if stage == 'unavailable' else 'complete')

    def unknown(values):
        return node('unavailable', 'Data insufficient', values, 'Missing, stale or incomparable inputs; no stage is inferred.',
                    'Unknown is not neutral or low risk.', 'Load complete, dated history and check its adjustment convention.')

    result = {}
    close, short, long = number(item.get('close_usd')) if eligible else None, value('sma20'), value('sma50')
    values = dict(close_usd=close, sma20=short, sma50=long, distance_sma20_pct=value('distance_sma20_pct'), distance_sma50_pct=value('distance_sma50_pct'))
    if None in (close, short, long):
        result['trend'] = unknown(values)
    else:
        stage = 'strong' if close > short > long else 'weak' if close < short < long else 'mixed'
        result['trend'] = node(stage, {'strong': 'Strong alignment', 'weak': 'Weak alignment', 'mixed': 'Mixed alignment'}[stage], values,
            {'strong': 'Close > SMA20 > SMA50.', 'weak': 'Close < SMA20 < SMA50.', 'mixed': 'Price and moving averages have no strict strong/weak ordering.'}[stage],
            'One alignment is not a confirmed persistent trend or reliable reversal.',
            'Watch whether the ordering persists or breaks on subsequent completed sessions.')
    rsi = value('rsi14')
    if rsi is None or not 0 <= rsi <= 100:
        result['rsi'] = unknown(dict(rsi14=rsi))
    else:
        stage, label = ('oversold', 'Traditional oversold reference') if rsi < 30 else ('weak', 'Weak momentum') if rsi < 45 else ('neutral', 'Neutral momentum') if rsi <= 55 else ('strong', 'Strong momentum') if rsi <= 70 else ('overbought', 'Traditional overbought reference')
        result['rsi'] = node(stage, label, dict(rsi14=rsi), 'RSI is classified by explicit reference boundaries.',
            'Overbought does not imply an imminent fall; oversold does not imply a rebound.',
            'Watch subsequent RSI and price behavior, together with your recorded thesis.',
            dict(traditional=[30, 70], product_partition=[45, 55], intervals='[0,30), [30,45), [45,55], (55,70], (70,100]'))
    macd, change = value('macd_signal_difference'), delta('macd_signal_difference')
    if macd is None:
        result['macd'] = unknown(dict(difference=macd, delta=change))
    else:
        direction = 'positive' if macd > 0 else 'negative' if macd < 0 else 'zero'
        state = 'unknown' if change is None else 'steady' if change == 0 or macd == 0 else 'strengthening' if macd * change > 0 else 'weakening'
        labels = {'positive_strengthening': 'Positive momentum strengthening', 'positive_weakening': 'Positive momentum weakening',
                  'negative_strengthening': 'Negative momentum strengthening', 'negative_weakening': 'Negative momentum weakening',
                  'positive_unknown': 'Positive difference; change unknown', 'negative_unknown': 'Negative difference; change unknown',
                  'positive_steady': 'Positive difference unchanged', 'negative_steady': 'Negative difference unchanged',
                  'zero_unknown': 'MACD difference at zero', 'zero_steady': 'MACD difference at zero'}
        stage = direction + '_' + state
        result['macd'] = node(stage, labels[stage], dict(difference=macd, delta=change),
            'The sign describes MACD minus signal; its adjacent-session delta describes expansion or contraction.',
            'A sign crossing alone is not a trade signal or a reliable reversal.',
            'Watch the next completed difference; a return across zero may invalidate this observation.', dict(reference_zero=0))
        result['macd']['comparison_quality'] = 'unavailable' if change is None else 'complete'
        result['macd']['sign_crossed'] = None if change is None else bool(macd * (macd - change) < 0)
    for key, metric in [('atr', 'atr_price_ratio'), ('volatility', 'historical_volatility20_pct')]:
        current, change = value(metric), delta(metric)
        if current is None:
            result[key] = unknown({metric: current, 'delta': change})
            continue
        stage = 'unknown' if change is None else 'rising' if change > 0 else 'falling' if change < 0 else 'steady'
        result[key] = node(stage, {'unknown': 'Change unavailable', 'rising': 'Scale increasing', 'falling': 'Scale decreasing', 'steady': 'Scale unchanged'}[stage],
            {metric: current, 'delta': change, **({'atr14_usd': value('atr14')} if key == 'atr' else {})},
            'ATR/price is a fractional measure of recent true range.' if key == 'atr' else 'Annualized sample standard deviation of the past 20 log returns.',
            'This is not a predicted loss or a universal high/low risk classification.',
            'Compare subsequent values and the same instrument’s longer history; no percentile is available.')
        result[key]['comparison_quality'] = 'unavailable' if change is None else 'complete'
    for kind, stage in result.items():
        stage.update(plain_explanation(kind, stage['stage_id'], lang))
    return result


def interpret_concentration(concentration, lang='en'):
    concentration = concentration or {}
    hhi = number(concentration.get('hhi'))
    available = concentration.get('status') == 'available' and hhi is not None and 0 < hhi <= 1
    result = dict(stage_id='measured' if available else 'unavailable', label=t('Market-value exposure' if available else 'Data insufficient', lang),
        session=concentration.get('valuation_session'), price_basis=concentration.get('price_basis'), quality='complete' if available else 'unavailable',
        values={key: concentration.get(key) if available else None for key in ('largest_weight_fraction', 'top_three_weight_fraction', 'hhi')} |
               {'effective_equal_weight_count': str(1 / hhi) if available else None}, thresholds={},
        reason=t('HHI sums squared market-value weights; 1/HHI is the equivalent equal-weight holding count.', lang),
        caveat=t('Equivalent count is not actual holding count. Market-value concentration does not measure sector, factor or constituent correlations.', lang),
        next_observation=t('Review dominant exposures alongside your thesis and verified company information; more tickers alone do not prove diversification.', lang))
    result.update(plain_explanation('exposure', result['stage_id'], lang))
    return result


def interpret_analysis(analysis, lang='en'):
    return dict(schema_version=1, language=lang, daily_session=analysis.get('expected_daily_session'),
                price_basis=analysis.get('price_basis'),
                instruments={i['symbol']: interpret_instrument(i, lang) for i in analysis.get('instruments', [])},
                exposure=interpret_concentration(analysis.get('portfolio_concentration'), lang))


def compact_badges(stages, lang='en'):
    """Display vocabulary only; all stage decisions are made above."""
    stages = stages or {}
    badges = []
    for key in ('trend', 'rsi', 'atr', 'macd'):
        stage = stages.get(key, {})
        if stage.get('stage_id', 'unavailable') == 'unavailable' or stage.get('quality') != 'complete' or stage.get('session') != stage.get('expected_session'):
            continue
        values = stage['values']
        label_key = {
            'trend': {'strong': 'Above averages', 'weak': 'Below averages', 'mixed': 'Mixed averages'},
            'rsi': {'oversold': 'Falls forceful', 'overbought': 'Gains forceful', 'weak': 'Weak momentum', 'neutral': 'Neutral momentum', 'strong': 'Strong momentum'},
        }.get(key, {}).get(stage['stage_id'], stage.get('label_id', stage['label']))
        label = t(label_key, lang)
        if key == 'trend':
            label = 'Trend · ' + label
        elif key == 'rsi':
            label = f"RSI {Decimal(values['rsi14']):.1f} · {label}"
        elif key == 'atr':
            ratio = Decimal(values['atr_price_ratio']) * 100
            arrow = {'rising': '↗', 'falling': '↘', 'steady': '→'}.get(stage['stage_id'], '')
            label = t('Daily swing {value}% {arrow}', lang, value=f'{ratio:.1f}', arrow=arrow).strip()
        else:
            label = 'MACD · ' + label
        tone = 'green' if stage['stage_id'] in ('strong', 'positive_strengthening') else 'red' if stage['stage_id'] in ('weak', 'negative_strengthening') else 'yellow' if stage['stage_id'] in ('overbought', 'oversold', 'mixed') else 'gray'
        badges.append(dict(kind=key, label=label, tone=tone, session=stage['session'],
                           detail=' · '.join([stage['reason'], stage['caveat']]),
                           explanation=plain_explanation(key, stage['stage_id'], lang)['plain_summary']))
    return badges


DEFINITIONS = {
    'trend': 'Moving-average alignment compares the close with the average closing prices of the past 20 and 50 sessions (SMA20/SMA50).',
    'rsi': 'RSI compares recent upward and downward price pressure on a 0–100 scale. 30/70 are traditional references; 45/55 are product explanation partitions.',
    'macd': 'MACD compares shorter- and longer-term price momentum. The difference from its signal line and its change describe momentum, not a trade instruction.',
    'atr': 'ATR is the recent average true price range, including overnight gaps. ATR/price expresses this day-to-day swing size relative to price: 0.032 means about 3.2%.',
    'volatility': 'Historical volatility annualizes how much past returns varied. It is neither a predicted return nor the percentage you are expected to lose.',
    'exposure': 'Largest and top-three weights show how much portfolio value they occupy. HHI summarizes weight concentration; 1/HHI is an equivalent equal-weight count, not the actual number of holdings.',
}
PLAIN_STAGES = {
    'trend': {
        'strong': 'The close is above the 20-day average, which is above the 50-day average; this does not prove the trend will persist.',
        'weak': 'The close is below the 20-day average, which is below the 50-day average; this does not confirm a lasting decline.',
        'mixed': 'The close and two averages do not point in one consistent direction; this does not confirm a reversal.',
    },
    'rsi': {
        'oversold': 'Recent declines have been forceful; oversold does not mean cheap or about to rebound.',
        'overbought': 'Recent gains have been forceful; overbought does not mean expensive or about to fall.',
        'weak': 'Recent downward pressure is stronger in this product partition; this is not a valuation judgement.',
        'neutral': 'Recent upward and downward pressure is relatively balanced in this product partition; that does not mean low risk.',
        'strong': 'Recent upward pressure is stronger in this product partition; that does not predict the next price move.',
    },
    'macd': {
        'positive_strengthening': 'The positive MACD-to-signal gap widened: upward momentum strengthened, without guaranteeing further gains.',
        'positive_weakening': 'The positive MACD-to-signal gap narrowed: upward momentum eased, without confirming a decline.',
        'negative_strengthening': 'The negative MACD-to-signal gap widened: downward momentum strengthened, without making further falls certain.',
        'negative_weakening': 'The negative MACD-to-signal gap moved toward zero: downward momentum eased, but has not turned positive.',
        'positive_steady': 'MACD remains above its signal with an unchanged gap; that does not predict further gains.',
        'negative_steady': 'MACD remains below its signal with an unchanged gap; that does not predict further declines.',
        'positive_unknown': 'MACD is above its signal; comparable prior data is missing, so strengthening cannot be assessed.',
        'negative_unknown': 'MACD is below its signal; comparable prior data is missing, so weakening cannot be assessed.',
        'zero_steady': 'MACD equals its signal; this alone does not establish a price direction.',
        'zero_unknown': 'MACD equals its signal; prior change is unknown and direction cannot be inferred.',
    },
    'atr': {
        'rising': 'ATR/price rose: recent day-to-day swings widened.',
        'falling': 'ATR/price fell: recent day-to-day swings narrowed.',
        'steady': 'ATR/price was unchanged: recent day-to-day swing size was similar.',
        'unknown': 'ATR/price describes recent day-to-day swing size; its change is unknown.',
    },
    'volatility': {
        'rising': 'Past return fluctuations increased on an annualized measure; this is not a forecast loss percentage.',
        'falling': 'Past return fluctuations decreased on an annualized measure; this does not guarantee future stability.',
        'steady': 'Annualized past return variability was unchanged; this is not a return forecast.',
        'unknown': 'Annualized past return variability is available, but its change cannot be compared; it is not a predicted loss.',
    },
    'exposure': {'measured': 'Large weights can dominate portfolio changes. An equivalent holding count does not reveal sector or correlation risk.'},
}


def plain_explanation(kind, stage_id, lang='en'):
    """Translate an existing stage, never classify values or fill missing inputs."""
    summary = PLAIN_STAGES.get(kind, {}).get(stage_id, 'Data is missing, stale or incomparable; no current state can be explained. Unknown does not mean low risk.')
    text = t(summary, lang)
    if kind == 'atr' and stage_id != 'unavailable':
        text += ' ' + t('This swing-size percentage is not a future loss, loss probability or price-direction forecast.', lang)
    return dict(definition=t(DEFINITIONS[kind], lang), plain_summary=text)
