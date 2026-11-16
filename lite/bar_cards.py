"""Pure HTML/CSS overview bars; unlike an embedded SVG they survive st.html sanitizing."""
from __future__ import annotations
import html
from datetime import date
from burning_sky.overview_visual import color_for_score, _num

MODELS=(('ECMWF IFS 0.25°','EC'),('GFS Global','GFS'))


def render(days,direction,title):
    records=[]
    peak=10.0
    for day in list(days)[:5]:
        pair=[]
        for model,label in MODELS:
            r=(day.get(direction) or {}).get(model) or {}
            scale=_num(r.get('burn_scale'))
            prob=_num(r.get('probability'))
            score=_num(r.get('color_score'))
            if scale is not None:peak=max(peak,max(0.,scale))
            pair.append((label,scale,prob,score))
        records.append((day.get('date',''),pair))
    ticks = ''.join(f'<div>{peak*i/4:.0f}%</div>' for i in (4,3,2,1,0))
    columns=[]
    for day,pair in records:
        try: dt=date.fromisoformat(str(day)).strftime('%m/%d')
        except (ValueError,TypeError):dt='—'
        bars=[]
        for label,scale,prob,score in pair:
            if scale is None or prob is None or score is None:
                bars.append('<div class="bs-lite-bar missing" title="暂无数据"></div>')
                continue
            scale=max(0.,scale);prob=max(0.,min(100.,prob))
            height=min(100.,scale/peak*100.)
            color=color_for_score(score)
            title_text=html.escape(f'{dt} {label} · 规模{scale:.1f}% · 概率{prob:.0f}% · 色彩分{score:.0f}',quote=True)
            bars.append(f'<div class="bs-lite-bar" title="{title_text}" style="height:{height:.3f}%;background:{color};opacity:{prob/100:.3f};border-color:{color}"></div>')
        columns.append('<div class="bs-lite-day"><div class="bs-lite-pair">'+''.join(bars)+
                       '</div><div class="bs-lite-daylabel">'+html.escape(dt)+'</div></div>')
    return f'''<div class="bs-lite-card"><div class="bs-lite-card-title">{html.escape(title)}</div>
      <div class="bs-lite-graph"><div class="bs-lite-ticks">{ticks}</div>
      <div class="bs-lite-axis"><div class="bs-lite-days">{''.join(columns)}</div></div></div>
      <div class="bs-lite-legend">EC　 ·　 GFS</div></div>'''


CSS='''<style>
.bs-lite-card{border-radius:22px;padding:22px 22px 18px;background:rgba(255,255,255,.82);color:#27344a;
box-shadow:0 12px 28px rgba(22,27,49,.12);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);border:1px solid rgba(255,255,255,.65);margin:14px 0;font-family:-apple-system,BlinkMacSystemFont,"Microsoft YaHei",sans-serif}
.bs-lite-card-title{font-size:19px;font-weight:750;padding:0 0 15px}
.bs-lite-graph{display:flex;gap:10px;align-items:stretch;height:209px}
.bs-lite-ticks{display:flex;flex-direction:column;justify-content:space-between;width:42px;color:#657188;font-size:11px;text-align:right;padding-bottom:26px;box-sizing:border-box}
.bs-lite-axis{flex:1;min-width:0;border-bottom:1px solid #9eabbc;background:repeating-linear-gradient(to bottom,rgba(80,93,116,.13) 0px,rgba(80,93,116,.13) 1px,transparent 1px,transparent 45.5px);}
.bs-lite-days{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));height:100%;gap:4px}
.bs-lite-day{height:100%;display:flex;flex-direction:column;align-items:center;min-width:0}
.bs-lite-pair{height:calc(100% - 25px);display:flex;align-items:flex-end;justify-content:center;gap:5px;width:100%;border-bottom:1px solid #a1adbc;padding:0 6px;box-sizing:border-box}
.bs-lite-bar{width:min(28px,37%);max-height:100%;min-height:0;border:1px solid;border-radius:6px 6px 0 0;box-sizing:border-box;transition:height .2s ease}
.bs-lite-bar.missing{height:11px;border:1px dashed #a4acb9;background:transparent;opacity:1;border-radius:3px}
.bs-lite-daylabel{height:25px;line-height:25px;color:#475569;font-size:12px;white-space:nowrap}
.bs-lite-legend{text-align:center;color:#657188;font-size:11px;margin-top:16px}
@media(max-width:520px){.bs-lite-card{padding:16px 10px 14px}.bs-lite-pair{gap:3px;padding:0 2px}.bs-lite-bar{width:min(22px,40%)}.bs-lite-ticks{width:34px;font-size:10px}.bs-lite-daylabel{font-size:10px}}
</style>'''
