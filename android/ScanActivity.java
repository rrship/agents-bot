package io.github.rrship.agentsbot;

import android.app.Activity;
import android.content.Intent;
import android.graphics.ImageFormat;
import android.graphics.Color;
import android.hardware.Camera;
import android.os.Bundle;
import android.view.Gravity;
import android.view.Surface;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.widget.FrameLayout;
import android.widget.TextView;
import com.google.zxing.BinaryBitmap;
import com.google.zxing.PlanarYUVLuminanceSource;
import com.google.zxing.ReaderException;
import com.google.zxing.common.HybridBinarizer;
import com.google.zxing.qrcode.QRCodeReader;
import java.io.IOException;
import java.util.List;

@SuppressWarnings("deprecation")
public final class ScanActivity extends Activity implements SurfaceHolder.Callback {
    private Camera camera;
    private SurfaceView preview;
    private SurfaceHolder surface;
    private TextView hint;
    private boolean active;
    private final QRCodeReader reader = new QRCodeReader();

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        FrameLayout frame = new FrameLayout(this);
        preview = new SurfaceView(this);
        frame.addView(preview);
        hint = new TextView(this);
        hint.setText("将电脑上的配对二维码放在画面中央");
        hint.setTextColor(Color.WHITE);
        hint.setTextSize(17);
        hint.setGravity(Gravity.CENTER);
        hint.setBackgroundColor(0xBB173F3B);
        hint.setPadding(16,32,16,48);
        frame.addView(hint, new FrameLayout.LayoutParams(-1,-2,Gravity.BOTTOM));
        preview.getHolder().addCallback(this);
        setContentView(frame);
    }

    @Override protected void onResume() { super.onResume(); active=true; startCamera(); }
    @Override protected void onPause() { active=false; releaseCamera(); super.onPause(); }
    @Override public void surfaceCreated(SurfaceHolder holder) { surface=holder; startCamera(); }
    @Override public void surfaceChanged(SurfaceHolder holder,int format,int width,int height) {}
    @Override public void surfaceDestroyed(SurfaceHolder holder) { surface=null; releaseCamera(); }

    private void releaseCamera() {
        if (camera != null) {
            camera.setPreviewCallback(null);
            camera.release();
            camera = null;
        }
    }

    private void startCamera() {
        if (!active || surface==null || camera!=null) return;
        try {
            Camera.CameraInfo info = new Camera.CameraInfo();
            int id=0;
            for (int i=0; i<Camera.getNumberOfCameras(); i++) {
                Camera.getCameraInfo(i,info);
                if (info.facing==Camera.CameraInfo.CAMERA_FACING_BACK) { id=i; break; }
            }
            Camera.getCameraInfo(id,info);
            camera=Camera.open(id);
            int rotation=getWindowManager().getDefaultDisplay().getRotation();
            int degrees=rotation==Surface.ROTATION_90?90:rotation==Surface.ROTATION_180?180:rotation==Surface.ROTATION_270?270:0;
            int angle=info.facing==Camera.CameraInfo.CAMERA_FACING_FRONT?
                      (360-(info.orientation+degrees)%360)%360:(info.orientation-degrees+360)%360;
            camera.setDisplayOrientation(angle);
            Camera.Parameters config=camera.getParameters();
            config.setPreviewFormat(ImageFormat.NV21);
            List<String> focus=config.getSupportedFocusModes();
            if (focus!=null && focus.contains(Camera.Parameters.FOCUS_MODE_CONTINUOUS_PICTURE))
                config.setFocusMode(Camera.Parameters.FOCUS_MODE_CONTINUOUS_PICTURE);
            camera.setParameters(config);
            camera.setPreviewDisplay(surface);
            camera.startPreview();
            camera.setOneShotPreviewCallback(this::decode);
        } catch (RuntimeException | IOException err) {
            releaseCamera();
            hint.setText("未能打开相机。请返回后重试，或粘贴配对邀请。");
        }
    }

    private void decode(byte[] frame, Camera source) {
        if (!active || camera!=source) return;
        Camera.Size s=source.getParameters().getPreviewSize();
        if (frame!=null && frame.length>=s.width*s.height) {
            PlanarYUVLuminanceSource luma=new PlanarYUVLuminanceSource(frame,s.width,s.height,0,0,s.width,s.height,false);
            try {
                String text=reader.decode(new BinaryBitmap(new HybridBinarizer(luma))).getText();
                MonitorActivity.validate(text);
                setResult(RESULT_OK,new Intent().putExtra("invite",text));
                finish();
                return;
            } catch (ReaderException ignored) {
                // Wait for a sharp frame.
            } catch (Exception invalid) {
                hint.setText("请扫描 Agents Bot 电脑端的配对二维码");
            } finally { reader.reset(); }
        }
        preview.postDelayed(()->{
            if (active && camera==source) camera.setOneShotPreviewCallback(this::decode);
        },140);
    }
}
