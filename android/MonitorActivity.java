package io.github.rrship.agentsbot;

import android.app.Activity;
import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.graphics.Color;
import android.os.Bundle;
import android.text.InputType;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.*;
import android.widget.*;
import java.net.URI;
import java.net.HttpURLConnection;
import java.net.URL;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MonitorActivity extends Activity {
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private LinearLayout column;
    private WebView monitor;
    private String bound;
    private int generation;
    private TextView feedback;

    private int dp(int n) { return Math.round(n * getResources().getDisplayMetrics().density); }
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        bound = getPreferences(MODE_PRIVATE).getString("device", "");
        if (bound.isEmpty()) pairing(); else dashboard();
    }
    private void layout() {
        generation++;
        if (monitor != null) { column.removeView(monitor); monitor.stopLoading(); monitor.destroy(); monitor = null; }
        column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setBackgroundColor(Color.rgb(247,247,242));
        column.setOnApplyWindowInsetsListener((v,i)->{
            v.setPadding(i.getSystemWindowInsetLeft(),i.getSystemWindowInsetTop(),
                         i.getSystemWindowInsetRight(),i.getSystemWindowInsetBottom());
            return i;
        });
        setContentView(column);
    }
    private TextView label(String s, int size) {
        TextView t = new TextView(this); t.setText(s); t.setTextSize(size);
        t.setTextColor(Color.rgb(23,63,59)); t.setPadding(dp(18),dp(12),dp(18),dp(8)); column.addView(t); return t;
    }
    private Button button(String s, Runnable click) {
        Button b = new Button(this); b.setText(s); column.addView(b);
        b.setOnClickListener(v -> click.run()); return b;
    }
    static String validate(String text) throws Exception {
        URI uri = new URI(text.trim());
        String ip = uri.getHost(), key = uri.getFragment();
        if (!"http".equals(uri.getScheme()) || ip == null || !ip.matches("[0-9.]+")
            || uri.getUserInfo()!=null || uri.getQuery()!=null || !("/".equals(uri.getPath()) || "/pair".equals(uri.getPath()))
            || uri.getPort()!=48763 || key==null || !key.matches("[A-Za-z0-9_-]{32,80}"))
            throw new Exception("请粘贴电脑复制的完整配对地址。");
        String[] parts=ip.split("\\.");
        if(parts.length!=4) throw new Exception("电脑地址无效。");
        int[] octets=new int[4];
        for(int i=0;i<4;i++){
            octets[i]=Integer.parseInt(parts[i]);
            if(octets[i]>255 || !Integer.toString(octets[i]).equals(parts[i])) throw new Exception("电脑地址无效。");
        }
        int a=octets[0], b=octets[1];
        if (!(a==10 || (a==192 && b==168) || (a==172 && b>=16 && b<=31) || (a==100 && b>=64 && b<=127)))
            throw new Exception("请选择局域网或 Tailscale 地址。");
        return uri.toASCIIString();
    }
    private void pairing() {
        layout(); label("绑定电脑",28);
        label("在电脑端点击「手机监控」，然后扫描屏幕上的二维码。",16);
        button("扫码绑定",()->{
            if(checkSelfPermission(Manifest.permission.CAMERA)==PackageManager.PERMISSION_GRANTED) openScanner();
            else requestPermissions(new String[]{Manifest.permission.CAMERA},11);
        });
        feedback=label("",15);
        EditText input=new EditText(this);
        input.setSingleLine(); input.setHint("电脑配对地址");
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        column.addView(input); input.setVisibility(View.GONE);
        button("粘贴配对邀请",()->{
            if(input.getVisibility()==View.VISIBLE){connect(input.getText().toString());return;}
            input.setVisibility(View.VISIBLE);input.requestFocus();feedback.setText("粘贴后再点一次即可连接。");
        });
        button("异地连接 · Tailscale",()->{
            Intent vpn=getPackageManager().getLaunchIntentForPackage("com.tailscale.ipn");
            if(vpn==null)vpn=new Intent(Intent.ACTION_VIEW,Uri.parse("https://tailscale.com/download/android"));
            try { startActivity(vpn); } catch(android.content.ActivityNotFoundException e){
                feedback.setText("请安装并打开 Tailscale，两端登录同一网络。");
            }
        });
    }
    private void openScanner(){startActivityForResult(new Intent(this,ScanActivity.class),12);}
    @Override public void onRequestPermissionsResult(int code,String[] permissions,int[] results){
        super.onRequestPermissionsResult(code,permissions,results);
        if(code==11 && results.length>0 && results[0]==PackageManager.PERMISSION_GRANTED)openScanner();
        else if(code==11 && feedback!=null)feedback.setText("扫码需要相机权限，也可以粘贴配对邀请。");
    }
    @Override protected void onActivityResult(int code,int result,Intent data){
        super.onActivityResult(code,result,data);
        if(code==12 && result==RESULT_OK && data!=null && feedback!=null)connect(data.getStringExtra("invite"));
    }
    private void connect(String text){
            final String link;
            try { link=validate(text); } catch(Exception e){feedback.setText("无法识别配对邀请。");return;}
            final int check=++generation;
            feedback.setText("正在验证电脑…");
            worker.execute(()->{
                String problem=null, device=link; HttpURLConnection c=null;
                try {
                    URI u=new URI(link);
                    boolean claim="/pair".equals(u.getPath());
                    c=(HttpURLConnection)new URL("http",u.getHost(),u.getPort(),claim?"/pair":"/progress").openConnection();
                    c.setConnectTimeout(4000); c.setReadTimeout(4000); c.setInstanceFollowRedirects(false);
                    if(claim)c.setRequestMethod("POST");
                    c.setRequestProperty("Authorization","Bearer "+u.getFragment());
                    int status=c.getResponseCode();
                    if(status==403)problem="配对邀请已失效。请在电脑上重新生成并扫码。";
                    else if(status!=200)throw new java.io.IOException();
                    else if(claim){
                        try(BufferedReader r=new BufferedReader(new InputStreamReader(c.getInputStream(),"US-ASCII"))){
                            char[] buf=new char[81];int len=0,n;
                            while(len<buf.length && (n=r.read(buf,len,buf.length-len))!=-1)len+=n;
                            device=validate("http://"+u.getHost()+":"+u.getPort()+"/#"+new String(buf,0,len));
                        }
                    }
                } catch(Exception e){problem="无法配对。请检查电脑监控开关、配对地址及两端网络。";}
                finally { if(c!=null)c.disconnect(); }
                final String msg=problem, saved=device;
                runOnUiThread(()->{ if(isDestroyed() || generation!=check)return;
                    if(msg!=null){feedback.setText(msg);return;}
                    bound=saved;getPreferences(MODE_PRIVATE).edit().putString("device",saved).apply();dashboard();});
            });
    }
    private void dashboard(){
        layout();
        LinearLayout bar=new LinearLayout(this);column.addView(bar);
        TextView title=new TextView(this);title.setText("已绑定 · "+URI.create(bound).getHost());
        title.setPadding(dp(12),0,0,0);bar.addView(title,new LinearLayout.LayoutParams(0,-2,1));
        Button retry=new Button(this);retry.setText("重连");bar.addView(retry);retry.setOnClickListener(v->dashboard());
        Button forget=new Button(this);forget.setText("解绑");bar.addView(forget);
        forget.setOnClickListener(v->{getPreferences(MODE_PRIVATE).edit().remove("device").apply();bound="";pairing();});
        TextView offline=label("电脑暂时离线，正在重连…",16);
        offline.setVisibility(View.GONE);
        monitor=new WebView(this);column.addView(monitor,new LinearLayout.LayoutParams(-1,0,1));
        monitor.getSettings().setAllowFileAccess(false);monitor.getSettings().setAllowContentAccess(false);
        monitor.getSettings().setJavaScriptEnabled(true);
        monitor.setWebViewClient(new WebViewClient(){
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request){return true;}
            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error){
                if(!request.isForMainFrame() || monitor!=view)return;
                offline.setVisibility(View.VISIBLE);view.setVisibility(View.INVISIBLE);
                offline.postDelayed(()->{
                    if(!isDestroyed() && monitor==view)view.loadUrl(bound);
                },4000);
            }
            @Override public void onPageCommitVisible(WebView view,String url){
                if(monitor!=view || !url.startsWith("http://") || offline.getVisibility()!=View.VISIBLE)return;
                view.evaluateJavascript("document.getElementById('connection') != null",result->{
                    if(monitor==view && "true".equals(result)){offline.setVisibility(View.GONE);view.setVisibility(View.VISIBLE);}
                });
            }
        });
        monitor.loadUrl(bound);
    }
    @Override public void onDestroy(){generation++;worker.shutdownNow();if(monitor!=null)monitor.destroy();super.onDestroy();}
}
