import Toybox.Application;
import Toybox.Communications;
import Toybox.Lang;
import Toybox.Time;
import Toybox.Time.Gregorian;

// Shared by the background refresher, the glance and the full view.
(:background, :glance)
module SwoleApi {
    const BASE = "https://personal.connectapi.garmin.com";
    const TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token";
    const FOOD_PATH = "/nutrition-service/food/logs/";

    // The personal API key, or null if it hasn't been filled in.
    function apiKey() {
        var key = Application.Properties.getValue("apiKey");
        if (key == null || !(key instanceof String) || key.length() < 10 || key.find("PASTE") != null) {
            return null;
        }
        return key;
    }

    // Today's date on the watch, as YYYY-MM-DD.
    function today() {
        var i = Gregorian.info(Time.now(), Time.FORMAT_SHORT);
        return Lang.format("$1$-$2$-$3$", [i.year, (i.month as Number).format("%02d"), (i.day as Number).format("%02d")]);
    }

    // 1840 -> "1,840"
    function fmt(n) {
        n = n.toNumber();
        var neg = n < 0;
        if (neg) {
            n = -n;
        }
        var s = "";
        while (n >= 1000) {
            s = "," + (n % 1000).format("%03d") + s;
            n = n / 1000;
        }
        s = n.toString() + s;
        return neg ? "-" + s : s;
    }

    // Last good reading: {"date", "goal", "eaten", "at"}, or null.
    function food() {
        try {
            return Application.Storage.getValue("food");
        } catch (e) {
            return null;
        }
    }

    function lastError() {
        try {
            return Application.Storage.getValue("error");
        } catch (e) {
            return null;
        }
    }

    function save(result) {
        try {
            if (result["error"] == null) {
                Application.Storage.setValue("food", result);
                Application.Storage.deleteValue("error");
            } else {
                Application.Storage.setValue("error", result["error"]);
            }
        } catch (e) {
            // Storage is read-only in some contexts; the next foreground refresh saves it.
        }
    }

    function errorText(err) {
        if (err == null) {
            return null;
        }
        if (err.equals("nokey")) {
            return "Add API key";
        }
        if (err == 401 || err == 403) {
            return "Key rejected";
        }
        if (err == -104 || err == -2) {
            return "Phone offline";
        }
        return "Sync error " + err.toString();
    }
}

// Exchanges the API key for a bearer token, then reads today's food log.
// Garmin's food log carries the calorie goal and what MyFitnessPal has synced as eaten.
(:background)
class SwoleFetcher {
    private var _key;
    private var _token;
    private var _done;

    function initialize(key, done) {
        _key = key;
        _token = null;
        _done = done;
    }

    function start() {
        Communications.makeWebRequest(
            SwoleApi.BASE + SwoleApi.TOKEN_PATH,
            {},
            {
                :method => Communications.HTTP_REQUEST_METHOD_POST,
                :headers => {
                    "Authorization" => "ApiKey " + _key,
                    "Content-Type" => Communications.REQUEST_CONTENT_TYPE_URL_ENCODED
                },
                :responseType => Communications.HTTP_RESPONSE_CONTENT_TYPE_TEXT_PLAIN
            },
            method(:onToken)
        );
    }

    function onToken(code, data) {
        if (code == 200 && data instanceof String) {
            _token = data;
            Communications.makeWebRequest(
                SwoleApi.BASE + SwoleApi.FOOD_PATH + SwoleApi.today(),
                null,
                {
                    :method => Communications.HTTP_REQUEST_METHOD_GET,
                    :headers => { "Authorization" => "Bearer " + _token },
                    :responseType => Communications.HTTP_RESPONSE_CONTENT_TYPE_JSON
                },
                method(:onFood)
            );
        } else {
            finish({ "error" => code });
        }
    }

    function onFood(code, data) {
        if (code != 200 || !(data instanceof Dictionary)) {
            finish({ "error" => code });
            return;
        }
        var goal = 0;
        var eaten = 0;
        var goals = data["dailyNutritionGoals"];
        var content = data["dailyNutritionContent"];
        if (goals instanceof Dictionary && goals["calories"] != null) {
            goal = goals["calories"].toNumber();
        }
        if (content instanceof Dictionary && content["calories"] != null) {
            eaten = content["calories"].toNumber();
        }
        finish({ "date" => SwoleApi.today(), "goal" => goal, "eaten" => eaten, "at" => Time.now().value() });
    }

    private function finish(result) {
        _done.invoke(result);
    }
}
