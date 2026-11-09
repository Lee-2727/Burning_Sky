"""Canonical single-mode color timeline renderer shared by Burning_Sky and Weather_Photography."""
def draw_dynamic_color_curve(samples, peak=None, direction_key="evening"):
    """Draw the model's per-sample RGB curve and color strip for the web UI.

    This is display-only: it uses the existing samples without changing forecast
    calculations, scores, brightness, or RGB values.
    """
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1400, 560
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 17)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        tiny = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    except Exception:
        font = small = tiny = ImageFont.load_default()

    left, right, top, bottom = 78, W - 28, 34, 385
    def x_at(i):
        return left + (right - left) * i / max(1, len(samples) - 1)
    def y_at(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 0.0
        return bottom - (bottom - top) * max(0.0, min(100.0, v)) / 100.0
    def rgb_of(point):
        c = point.get("color", (190, 125, 105))
        if not isinstance(c, (list, tuple)) or len(c) < 3:
            c = (190, 125, 105)
        return tuple(max(0, min(255, int(v))) for v in c[:3])

    # The vertical axis is relative brightness only. Hue is taken independently
    # from each model sample's predicted RGB (solar-angle-based), never mapped
    # from the Y value.
    d.text((left, 12), "相对亮度", font=tiny, fill=(71, 85, 105), anchor="la")
    # Grid and Y axis labels.
    for val in (0, 20, 40, 60, 80, 100):
        y = y_at(val)
        d.line((left, y, right, y), fill=(220, 226, 234), width=1)
        d.text((left - 12, y - 8), str(val), font=small, fill=(71, 85, 105), anchor="ra")
    d.line((left, top, left, bottom), fill=(100, 116, 139), width=2)
    d.line((left, bottom, right, bottom), fill=(100, 116, 139), width=2)

    if samples:
        # Use each sample's own estimated RGB for its segment, matching the desktop UI.
        for i in range(len(samples) - 1):
            # 严格几何光路无效的样本作为断点，不跨断点连线或填充面积。
            if not samples[i].get("geometry_valid", False) or not samples[i + 1].get("geometry_valid", False):
                continue
            x0, x1 = x_at(i), x_at(i + 1)
            y0 = y_at(samples[i].get("relative_brightness", 0))
            y1 = y_at(samples[i + 1].get("relative_brightness", 0))
            c = rgb_of(samples[i])
            fill = tuple(int(255 * 0.08 + c[k] * 0.92) for k in range(3))
            d.polygon([(x0, bottom), (x0, y0), (x1, y1), (x1, bottom)], fill=fill)
            d.line((x0, y0, x1, y1), fill=c, width=5)
        # 仅绘制有效几何点；无效时段保持真实空白。
        for i, point in enumerate(samples):
            if not point.get("geometry_valid", False):
                continue
            x, y = x_at(i), y_at(point.get("relative_brightness", 0))
            c = rgb_of(point)
            d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=c, outline=c)

        # Sunrise/sunset event marker and evenly spaced local-time labels.
        event_i = min(range(len(samples)), key=lambda i: abs(float(samples[i].get("minutes_from_event", 999999))))
        event_x = x_at(event_i)
        d.line((event_x, top, event_x, bottom), fill=(100, 116, 139), width=1)
        for tick in range(7):
            i = round((len(samples) - 1) * tick / 6)
            x = x_at(i)
            d.line((x, bottom, x, bottom + 7), fill=(150, 160, 175), width=1)
            dt = samples[i].get("datetime")
            label = dt.strftime("%H:%M") if hasattr(dt, "strftime") else str(samples[i].get("minutes_from_event", ""))
            if i == event_i:
                label += " SUNRISE" if direction_key == "morning" else " SUNSET"
            d.text((x, bottom + 12), label, font=tiny, fill=(51, 65, 85), anchor="ma")

        if peak and isinstance(peak, dict):
            try:
                peak_dt = peak.get("datetime")
                if peak_dt is not None and hasattr(peak_dt, "timestamp"):
                    pi = min(range(len(samples)), key=lambda i: abs((samples[i]["datetime"] - peak_dt).total_seconds()))
                else:
                    pi = min(range(len(samples)), key=lambda i: abs(float(samples[i].get("minutes_from_event", 0)) - float(peak.get("minutes_from_event", 0))))
                px, py = x_at(pi), y_at(samples[pi].get("relative_brightness", 0))
                if samples[pi].get("geometry_valid", False):
                    d.ellipse((px - 7, py - 7, px + 7, py + 7), fill=(255, 255, 255), outline=rgb_of(samples[pi]), width=3)
            except Exception:
                pass
    else:
        # Empty-but-valid forecast: keep the axes and zero baseline visible.
        d.line((left, bottom, right, bottom), fill=(148, 163, 184), width=2)

    # The strip follows the exact sample RGB sequence. With no samples, show a neutral placeholder.
    strip_y0, strip_y1 = 450, 478
    if samples:
        for i, point in enumerate(samples):
            xa = int(x_at(i))
            xb = int(x_at(i + 1)) if i + 1 < len(samples) else right
            if point.get("geometry_valid", False):
                d.rectangle((xa, strip_y0, max(xa + 1, xb), strip_y1), fill=rgb_of(point))
    else:
        d.rectangle((left, strip_y0, right, strip_y1), fill=(235, 235, 235))
    d.text((left, 492), "Estimated color by time (model RGB; no extra saturation enhancement)", font=small, fill=(71, 85, 105))
    d.text((left, 525), "Relative brightness (window peak = 100)", font=small, fill=(71, 85, 105))
    return img
