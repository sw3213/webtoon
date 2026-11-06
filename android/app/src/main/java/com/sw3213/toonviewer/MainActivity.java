package com.sw3213.toonviewer;

import android.app.Activity;
import android.content.Intent;
import android.content.res.Configuration;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 화면은 assets/index.html (웹 화면), 폴더 읽기와 이미지 제공은 Java 가 담당.
 * 웹 화면은 https://app.local/ 주소로 열리고, 그 주소로 오는 요청을 여기서 직접 응답한다.
 */
public class MainActivity extends Activity {

    private static final String HOST = "app.local";
    private static final int REQ_TREE = 1;

    private WebView web;
    private Library lib;
    private final ExecutorService pool = Executors.newFixedThreadPool(2);

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        lib = new Library(this);

        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#141312"));
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setMediaPlaybackRequiresUserGesture(true);

        web.addJavascriptInterface(new Bridge(), "Android");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                if (!HOST.equals(u.getHost())) return null;
                return route(u);
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                // 앱 밖 주소로는 이동하지 않음
                return !HOST.equals(req.getUrl().getHost());
            }
        });

        if (state != null) web.restoreState(state);
        else web.loadUrl("https://" + HOST + "/index.html");
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    @Override
    public void onConfigurationChanged(Configuration c) {
        super.onConfigurationChanged(c);
    }

    @Override
    public void onBackPressed() {
        web.evaluateJavascript("window.appBack ? appBack() : false", r -> {
            if (!"true".equals(r)) finish();   // 서재 화면에서 뒤로 = 앱 종료
        });
    }

    @Override
    protected void onDestroy() {
        pool.shutdownNow();
        super.onDestroy();
    }

    // ------------------------------------------------------------ 요청 처리
    private WebResourceResponse route(Uri u) {
        String path = u.getPath() == null ? "/" : u.getPath();
        try {
            if (path.equals("/") || path.equals("/index.html")) {
                return ok("text/html", getAssets().open("index.html"), false);
            }
            if (path.equals("/img")) {
                Library.Img img = lib.image(u.getQueryParameter("ep"), Integer.parseInt(u.getQueryParameter("i")));
                if (img != null && img.in != null) return ok(img.mime, img.in, true);
            }
            if (path.equals("/cover")) {
                File f = lib.cover(u.getQueryParameter("s"));
                if (f != null) return ok("image/jpeg", new FileInputStream(f), true);
            }
        } catch (Exception ignored) {
        }
        return notFound();
    }

    private static WebResourceResponse ok(String mime, InputStream in, boolean cache) {
        WebResourceResponse r = new WebResourceResponse(mime, mime.startsWith("text/") ? "utf-8" : null, in);
        Map<String, String> h = new HashMap<>();
        h.put("Cache-Control", cache ? "max-age=86400" : "no-cache");
        r.setResponseHeaders(h);
        return r;
    }

    private static WebResourceResponse notFound() {
        return new WebResourceResponse("text/plain", "utf-8", 404, "Not Found",
                new HashMap<>(), new ByteArrayInputStream(new byte[0]));
    }

    // ------------------------------------------------------------ 폴더 고르기
    @Override
    protected void onActivityResult(int req, int result, Intent data) {
        super.onActivityResult(req, result, data);
        if (req != REQ_TREE) return;
        if (result == RESULT_OK && data != null && data.getData() != null) {
            Uri tree = data.getData();
            try {
                getContentResolver().takePersistableUriPermission(tree, Intent.FLAG_GRANT_READ_URI_PERMISSION);
            } catch (SecurityException ignored) {
            }
            lib.addRoot(tree.toString());
            web.evaluateJavascript("window.onFolderPicked && onFolderPicked(true)", null);
        } else {
            web.evaluateJavascript("window.onFolderPicked && onFolderPicked(false)", null);
        }
    }

    // ------------------------------------------------------------ 웹 화면 → Java
    private void reply(String cb, String json) {
        String js = "window.__cb(" + JSONObject.quote(cb) + "," + JSONObject.quote(json) + ")";
        runOnUiThread(() -> web.evaluateJavascript(js, null));
    }

    private class Bridge {
        /** 오래 걸리는 작업은 비동기로: 끝나면 window.__cb(cb, 결과JSON) 호출 */
        @JavascriptInterface
        public void call(String method, String arg, String cb) {
            pool.execute(() -> {
                String out;
                try {
                    switch (method) {
                        case "library":
                            if ("scan".equals(arg) || !lib.hasCache()) lib.scan();
                            out = lib.libraryJson();
                            break;
                        case "series":
                            out = lib.seriesJson(arg);
                            break;
                        case "episode":
                            out = lib.episodeJson(arg);
                            break;
                        case "roots":
                            out = lib.rootsJson();
                            break;
                        default:
                            out = "null";
                    }
                } catch (Exception e) {
                    out = "{\"error\":" + JSONObject.quote(String.valueOf(e.getMessage())) + "}";
                }
                reply(cb, out);
            });
        }

        @JavascriptInterface
        public void pickFolder() {
            runOnUiThread(() -> {
                Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
                i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
                try {
                    startActivityForResult(i, REQ_TREE);
                } catch (Exception e) {
                    web.evaluateJavascript("window.onFolderPicked && onFolderPicked(false)", null);
                }
            });
        }

        @JavascriptInterface
        public void removeFolder(String uri) {
            lib.removeRoot(uri);
            try {
                getContentResolver().releasePersistableUriPermission(Uri.parse(uri), Intent.FLAG_GRANT_READ_URI_PERMISSION);
            } catch (Exception ignored) {
            }
        }

        /** 보는 화면에서 상태바/내비게이션 바 숨기기 */
        @JavascriptInterface
        public void immersive(boolean on) {
            runOnUiThread(() -> {
                Window w = getWindow();
                if (android.os.Build.VERSION.SDK_INT >= 30) {
                    WindowInsetsController c = w.getInsetsController();
                    if (c == null) return;
                    if (on) {
                        c.hide(WindowInsets.Type.statusBars() | WindowInsets.Type.navigationBars());
                        c.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
                    } else {
                        c.show(WindowInsets.Type.statusBars() | WindowInsets.Type.navigationBars());
                    }
                } else {
                    w.getDecorView().setSystemUiVisibility(on
                            ? View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                            : View.SYSTEM_UI_FLAG_VISIBLE);
                }
            });
        }
    }
}
