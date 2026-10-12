package xyz.bichig.book2course;

import android.Manifest;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.CookieManager;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;

/**
 * Thin WebView shell around the Book2Course web app (https://hel.bichig.xyz).
 *
 * It bridges what a plain WebView cannot do on its own:
 *   1. Microphone for the Live voice tutor: the page's getUserMedia request is granted
 *      once the RECORD_AUDIO runtime permission is given.
 *   2. File picker for <input type="file"> (admins upload PDF textbooks).
 *   3. Payments: paylink.mn stays inside the app (Back returns to the course); links that
 *      open bank/wallet apps (intent:, qpay:, …) are handed to Android.
 * Other websites open in the phone's browser. The user agent ends with "Book2CourseApp/<version>"
 * so the web app can tell it is running inside the app (e.g. to hide the "Download app" box).
 */
public class MainActivity extends Activity {

    private static final String APP_URL = "https://hel.bichig.xyz/";
    private static final String APP_HOST = "hel.bichig.xyz";
    private static final String PAY_HOST = "paylink.mn";
    private static final int REQ_MIC = 1;
    private static final int REQ_FILE = 2;

    private WebView web;
    private TextView offline;
    private PermissionRequest pendingMicRequest;
    private ValueCallback<Uri[]> filePathCallback;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Android 15+ draws apps edge to edge; pad the content so it never sits under the
        // status bar, the navigation bar or the keyboard.
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#22483D"));
        root.setOnApplyWindowInsetsListener((v, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets i = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.ime());
                v.setPadding(i.left, i.top, i.right, i.bottom);
            } else {
                v.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                    insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            }
            return insets;
        });

        web = new WebView(this);
        web.setBackgroundColor(Color.WHITE);
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));

        offline = new TextView(this);
        offline.setText(R.string.offline);
        offline.setTextColor(Color.WHITE);
        offline.setTextSize(17);
        offline.setPadding(64, 64, 64, 64);
        offline.setGravity(android.view.Gravity.CENTER);
        offline.setVisibility(View.GONE);
        offline.setOnClickListener(v -> { offline.setVisibility(View.GONE); web.reload(); });
        root.addView(offline, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);   // the tutor's voice plays without an extra tap
        s.setUserAgentString(s.getUserAgentString() + " Book2CourseApp/" + versionName());
        CookieManager.getInstance().setAcceptCookie(true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri url = request.getUrl();
                String scheme = url.getScheme() == null ? "" : url.getScheme();
                String host = url.getHost() == null ? "" : url.getHost();
                if (scheme.equals("https") && (host.equals(APP_HOST) || host.equals(PAY_HOST) || host.endsWith("." + PAY_HOST))) {
                    return false;                       // stay in the app
                }
                openOutside(url);
                return true;
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) offline.setVisibility(View.VISIBLE);
            }
        });

        web.setWebChromeClient(new WebChromeClient() {
            // Live voice: the page asks for the microphone.
            @Override
            public void onPermissionRequest(PermissionRequest request) {
                boolean wantsMic = false;
                for (String r : request.getResources()) {
                    if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(r)) wantsMic = true;
                }
                if (!wantsMic || !APP_HOST.equals(request.getOrigin().getHost())) {
                    request.deny();
                    return;
                }
                if (hasMicPermission()) {
                    request.grant(new String[] { PermissionRequest.RESOURCE_AUDIO_CAPTURE });
                } else {
                    if (pendingMicRequest != null) pendingMicRequest.deny();
                    pendingMicRequest = request;
                    requestPermissions(new String[] { Manifest.permission.RECORD_AUDIO }, REQ_MIC);
                }
            }

            // <input type="file">: open Android's file picker.
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (filePathCallback != null) filePathCallback.onReceiveValue(null);
                filePathCallback = callback;
                try {
                    Intent intent = params.createIntent();
                    intent.addCategory(Intent.CATEGORY_OPENABLE);
                    startActivityForResult(intent, REQ_FILE);
                } catch (Exception e) {
                    filePathCallback = null;
                    return false;
                }
                return true;
            }
        });

        if (savedInstanceState != null) {
            web.restoreState(savedInstanceState);
        } else {
            Uri link = getIntent() == null ? null : getIntent().getData();
            web.loadUrl(link != null && APP_HOST.equals(link.getHost()) ? link.toString() : APP_URL);
        }
    }

    /** Bank/wallet app links (intent:, qpay:, …) and other websites go outside the app. */
    private void openOutside(Uri url) {
        try {
            Intent intent = "intent".equals(url.getScheme())
                ? Intent.parseUri(url.toString(), Intent.URI_INTENT_SCHEME)
                : new Intent(Intent.ACTION_VIEW, url);
            intent.addCategory(Intent.CATEGORY_BROWSABLE);
            intent.setComponent(null);
            intent.setSelector(null);
            startActivity(intent);
        } catch (ActivityNotFoundException e) {
            // e.g. a bank app that is not installed: use the store/web fallback the link provides.
            try {
                Intent parsed = Intent.parseUri(url.toString(), Intent.URI_INTENT_SCHEME);
                String fallback = parsed.getStringExtra("browser_fallback_url");
                if (fallback != null) web.loadUrl(fallback);
            } catch (Exception ignored) {}
        } catch (Exception ignored) {}
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        Uri link = intent.getData();
        if (link != null && APP_HOST.equals(link.getHost())) web.loadUrl(link.toString());
    }

    private boolean hasMicPermission() {
        return checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
    }

    private String versionName() {
        try {
            return getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (Exception e) {
            return "";
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        if (requestCode == REQ_MIC && pendingMicRequest != null) {
            if (hasMicPermission()) {
                pendingMicRequest.grant(new String[] { PermissionRequest.RESOURCE_AUDIO_CAPTURE });
            } else {
                pendingMicRequest.deny();
            }
            pendingMicRequest = null;
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQ_FILE && filePathCallback != null) {
            Uri[] results = resultCode == RESULT_OK && data != null
                ? WebChromeClient.FileChooserParams.parseResult(resultCode, data) : null;
            filePathCallback.onReceiveValue(results);
            filePathCallback = null;
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush();          // keep the student signed in
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }
}
