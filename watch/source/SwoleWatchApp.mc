import Toybox.Application;
import Toybox.Background;
import Toybox.Lang;
import Toybox.System;
import Toybox.Time;
import Toybox.WatchUi;

(:background)
class SwoleWatchApp extends Application.AppBase {
    function initialize() {
        AppBase.initialize();
    }

    function getInitialView() {
        scheduleRefresh();
        return [new SwoleView()];
    }

    (:glance)
    function getGlanceView() {
        scheduleRefresh();
        return [new SwoleGlanceView()];
    }

    function getServiceDelegate() {
        return [new SwoleServiceDelegate()];
    }

    // Results from the background refresher arrive here, in the glance or the full view.
    function onBackgroundData(data) {
        if (data instanceof Dictionary) {
            SwoleApi.save(data);
        }
        WatchUi.requestUpdate();
    }

    // Once registered, the refresh keeps running every 15 minutes, even with the app closed.
    function scheduleRefresh() {
        if (Background.getTemporalEventRegisteredTime() == null) {
            Background.registerForTemporalEvent(new Time.Duration(15 * 60));
        }
    }
}

(:background)
class SwoleServiceDelegate extends System.ServiceDelegate {
    private var _fetcher;

    function initialize() {
        ServiceDelegate.initialize();
    }

    function onTemporalEvent() {
        var key = SwoleApi.apiKey();
        if (key == null) {
            Background.exit({ "error" => "nokey" });
            return;
        }
        _fetcher = new SwoleFetcher(key, method(:onResult));
        _fetcher.start();
    }

    function onResult(result) {
        Background.exit(result);
    }
}
