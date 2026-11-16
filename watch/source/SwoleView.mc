import Toybox.Graphics;
import Toybox.Lang;
import Toybox.Time;
import Toybox.Time.Gregorian;
import Toybox.WatchUi;

// Full screen: a ring filling toward the calorie goal, eaten in the middle, remaining below.
class SwoleView extends WatchUi.View {
    private var _fetcher;
    private var _syncing = false;

    function initialize() {
        View.initialize();
    }

    // Refresh straight away whenever the widget is opened.
    function onShow() {
        var key = SwoleApi.apiKey();
        if (key == null) {
            SwoleApi.save({ "error" => "nokey" });
            return;
        }
        _syncing = true;
        _fetcher = new SwoleFetcher(key, method(:onResult));
        _fetcher.start();
    }

    function onResult(result) {
        _syncing = false;
        SwoleApi.save(result);
        WatchUi.requestUpdate();
    }

    function onUpdate(dc) {
        var w = dc.getWidth();
        var h = dc.getHeight();
        var cx = w / 2;
        var cy = h / 2;
        dc.setColor(Graphics.COLOR_BLACK, Graphics.COLOR_BLACK);
        dc.clear();

        var food = SwoleApi.food();
        var fresh = food != null && SwoleApi.today().equals(food["date"]);
        var eaten = fresh ? food["eaten"] : 0;
        var goal = fresh ? food["goal"] : 0;

        // Ring: dark track, blue progress (orange once past the goal).
        var r = cx - 9;
        dc.setPenWidth(10);
        dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawCircle(cx, cy, r);
        if (goal > 0 && eaten > 0) {
            var pct = eaten.toFloat() / goal;
            dc.setColor(pct > 1.0 ? Graphics.COLOR_ORANGE : Graphics.COLOR_BLUE, Graphics.COLOR_TRANSPARENT);
            if (pct >= 1.0) {
                dc.drawCircle(cx, cy, r);
            } else {
                var end = 90 - (pct * 360).toNumber();
                if (end < 0) {
                    end += 360;
                }
                dc.drawArc(cx, cy, r, Graphics.ARC_CLOCKWISE, 90, end);
            }
        }
        dc.setPenWidth(1);

        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(cx, cy - h * 0.30, Graphics.FONT_XTINY, "EATEN TODAY", Graphics.TEXT_JUSTIFY_CENTER);

        if (!fresh) {
            var err = SwoleApi.errorText(SwoleApi.lastError());
            dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
            dc.drawText(cx, cy, Graphics.FONT_SMALL, _syncing ? "Syncing..." : (err != null ? err : "No data yet"),
                Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);
            return;
        }

        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(cx, cy - h * 0.06, Graphics.FONT_NUMBER_MEDIUM, SwoleApi.fmt(eaten),
            Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);

        if (goal > 0) {
            dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(cx, cy + h * 0.10, Graphics.FONT_TINY, "of " + SwoleApi.fmt(goal) + " kcal",
                Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);
            var left = goal - eaten;
            dc.setColor(left >= 0 ? Graphics.COLOR_GREEN : Graphics.COLOR_ORANGE, Graphics.COLOR_TRANSPARENT);
            dc.drawText(cx, cy + h * 0.21, Graphics.FONT_SMALL,
                left >= 0 ? SwoleApi.fmt(left) + " left" : SwoleApi.fmt(-left) + " over",
                Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);
        }

        var t = Gregorian.info(new Time.Moment(food["at"]), Time.FORMAT_SHORT);
        dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(cx, cy + h * 0.33, Graphics.FONT_XTINY,
            (_syncing ? "Syncing - " : "") + Lang.format("$1$:$2$", [t.hour, (t.min as Number).format("%02d")]),
            Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);
    }
}
