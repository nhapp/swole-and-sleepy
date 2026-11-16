import Toybox.Graphics;
import Toybox.Lang;
import Toybox.WatchUi;

// The one-line view in the widget loop: "1,840 / 2,350" with a progress bar.
(:glance)
class SwoleGlanceView extends WatchUi.GlanceView {
    function initialize() {
        GlanceView.initialize();
    }

    function onUpdate(dc) {
        var w = dc.getWidth();
        var h = dc.getHeight();
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(0, 0, Graphics.FONT_GLANCE, "FUEL", Graphics.TEXT_JUSTIFY_LEFT);

        var food = SwoleApi.food();
        var fresh = food != null && SwoleApi.today().equals(food["date"]);
        if (!fresh) {
            var err = SwoleApi.errorText(SwoleApi.lastError());
            dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(0, h * 0.38, Graphics.FONT_GLANCE, err != null ? err : "Open to sync", Graphics.TEXT_JUSTIFY_LEFT);
            return;
        }

        var eaten = food["eaten"];
        var goal = food["goal"];
        var text = goal > 0 ? SwoleApi.fmt(eaten) + " / " + SwoleApi.fmt(goal) : SwoleApi.fmt(eaten) + " kcal";
        dc.drawText(0, h * 0.38, Graphics.FONT_GLANCE_NUMBER, text, Graphics.TEXT_JUSTIFY_LEFT);

        if (goal > 0) {
            var barY = h - 6;
            var pct = eaten.toFloat() / goal;
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.fillRoundedRectangle(0, barY, w, 5, 2);
            dc.setColor(pct > 1.0 ? Graphics.COLOR_ORANGE : Graphics.COLOR_BLUE, Graphics.COLOR_TRANSPARENT);
            var fill = (pct > 1.0 ? 1.0 : pct) * w;
            if (fill >= 2) {
                dc.fillRoundedRectangle(0, barY, fill, 5, 2);
            }
        }
    }
}
