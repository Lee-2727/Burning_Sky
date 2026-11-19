"""Pure HTML/CSS overview bars; unlike an embedded SVG they survive st.html sanitizing."""
from __future__ import annotations
import html
from datetime import date
from burning_sky.overview_visual import color_for_score, _num

MODELS=(('ECMWF IFS 0.25°','EC'),('GFS Global','GFS'))


def render(days,direction,title):
    specs=(('ECMWF IFS 0.25°','EC','burn_scale','几何'),
           ('ECMWF IFS 0.25°','EC','visual_burn_scale','视觉'),
           ('GFS Global','GFS','burn_scale','几何'),
           ('GFS Global','GFS','visual_burn_scale','视觉'))
    records=[]; peak=10.0
    for day in list(days)[:3]:
        pair=[]
        for model,label,key,kind in specs:
            r=(day.get(direction) or {}).get(model) or {}
            scale=_num(r.get(key)); prob=_num(r.get('probability')); score=_num(r.get('color_score'))
            if scale is not None:peak=max(peak,max(0.,scale))
            pair.append((label,kind,scale,prob,score))
        records.append((day.get('date',''),pair))
    ticks=''.join(f'<div>{peak*i/4:.0f}%</div>' for i in (4,3,2,1,0))
    columns=[]
    for day,pair in records:
        try:dt=date.fromisoformat(str(day)).strftime('%m/%d')
        except (ValueError,TypeError):dt='—'
        bars=[]
        for label,kind,scale,prob,score in pair:
            if scale is None or prob is None or score is None:
                bars.append('<div class="bs-lite-bar missing" title="暂无数据">—</div>'); continue
            scale=max(0.,scale); prob=max(0.,min(100.,prob)); color=color_for_score(score)
            tip=html.escape(f'{dt} 火烧云规模·{label}{kind}{scale:.1f}% · 常规概率{prob:.0f}% · 色彩分{score:.0f}',quote=True)
            if prob<=0. or scale<=0.:
                bars.append(f'<div class="bs-lite-bar empty" title="{tip}"></div>')
            else:
                height=min(100.,scale/peak*100.)
                border='border:1px solid rgba(244,246,251,.5);' if kind=='视觉' else ''
                bars.append(f'<div class="bs-lite-bar has-data" title="{tip}" style="height:{height:.4f}%;{border}">'
                            f'<div class="bs-lite-bar-fill" style="background:{color};opacity:{prob/100:.4f}"></div></div>')
        columns.append('<div class="bs-lite-day"><div class="bs-lite-pair">'+''.join(bars)+
                       '</div><div class="bs-lite-daylabel">'+html.escape(dt)+'</div></div>')
    return f"""<div class="bs-lite-card"><div class="bs-lite-card-title">{html.escape(title)}</div>
      <div class="bs-lite-graph"><div class="bs-lite-ticks">{ticks}</div>
      <div class="bs-lite-axis"><div class="bs-lite-days">{''.join(columns)}</div></div></div>
      <div class="bs-lite-legend">EC几何　EC视觉　GFS几何　GFS视觉</div></div>"""


CSS='''<style>
/* Lite keeps its frosted glass card; the colored bars do not inherit any extra opacity. */
.bs-lite-card{border-radius:22px;padding:20px 22px 16px;background:rgba(255,255,255,.07);color:#F4F6FB;
box-shadow:0 14px 34px rgba(8,10,20,.22);backdrop-filter:blur(22px) saturate(150%);-webkit-backdrop-filter:blur(22px) saturate(150%);border:1px solid rgba(255,255,255,.12);margin:14px 0;font-family:-apple-system,BlinkMacSystemFont,"Microsoft YaHei",sans-serif}
.bs-lite-card-title{font-size:17px;font-weight:660;letter-spacing:-.01em;padding:0 0 15px;color:#F4F6FB}
.bs-lite-graph{display:flex;gap:10px;align-items:stretch;height:209px}
.bs-lite-ticks{display:flex;flex-direction:column;justify-content:space-between;width:42px;color:rgba(244,246,251,.46);font-size:11px;text-align:right;padding-bottom:26px;box-sizing:border-box}
.bs-lite-axis{flex:1;min-width:0;border-bottom:1px solid rgba(255,255,255,.10);background:repeating-linear-gradient(to bottom,rgba(255,255,255,.06) 0px,rgba(255,255,255,.06) 1px,transparent 1px,transparent 45.5px);}
.bs-lite-days{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));height:100%;gap:4px}
.bs-lite-day{height:100%;display:flex;flex-direction:column;align-items:center;min-width:0}
.bs-lite-pair{height:calc(100% - 25px);display:flex;align-items:flex-end;justify-content:center;gap:5px;width:100%;border-bottom:1px solid rgba(255,255,255,.10);padding:0 6px;box-sizing:border-box}
.bs-lite-bar{width:min(20px,23%);max-height:100%;min-height:0;border:0;border-radius:6px 6px 2px 2px;box-sizing:border-box;position:relative;overflow:hidden}
.bs-lite-bar-fill{position:absolute;inset:0;border-radius:5px 5px 1px 1px}
.bs-lite-bar.empty{height:0;border:0;background:transparent;box-shadow:none}
.bs-lite-bar.missing{height:11px;border:1px dashed rgba(244,246,251,.32);background:transparent;opacity:1;border-radius:3px}
.bs-lite-daylabel{height:25px;line-height:25px;color:rgba(244,246,251,.72);font-size:12px;white-space:nowrap}
.bs-lite-legend{text-align:center;color:rgba(244,246,251,.46);font-size:11px;margin-top:16px;letter-spacing:.02em}
@media(max-width:520px){.bs-lite-card{padding:16px 12px 14px;border-radius:18px}.bs-lite-pair{gap:3px;padding:0 2px}.bs-lite-bar{width:min(17px,24%)}.bs-lite-ticks{width:34px;font-size:10px}.bs-lite-daylabel{font-size:10px}}
.bs-lite-bar.missing{height:19px;width:23px;font-size:12px;display:flex;align-items:center;justify-content:center;color:rgba(244,246,251,.5);background:transparent;opacity:1}
</style>'''
