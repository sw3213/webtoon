package com.sw3213.toonviewer;

import android.content.ContentResolver;
import android.content.Context;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.BitmapRegionDecoder;
import android.graphics.Rect;
import android.net.Uri;
import android.provider.DocumentsContract;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * 사용자가 고른 폴더(들)를 훑어서 작품/회차를 정리한다.
 *
 * 인식하는 구조
 *   폴더/작품/회차/001.jpg      (탬퍼몽키 폴더 저장)
 *   폴더/작품/001 작품 1화.zip  (Python 다운로더)
 *   폴더/작품 1화.zip           (ZIP 저장, 이름에서 작품명 추출)
 *   폴더/작품 1화/001.jpg
 */
class Library {

    static class Ep {
        String id, name, seriesId, uri;
        boolean zip;
        int num;
        List<String> images = new ArrayList<>();   // 폴더형: 이미지 문서 URI 목록
    }

    static class Series {
        String id, name;
        long mtime;
        List<Ep> eps = new ArrayList<>();
    }

    private static final String PREFS = "toonviewer";
    private static final int MAX_DEPTH = 2;
    private static final int KEEP_EXTRACTED = 6;
    private static final Pattern EP_NUM = Pattern.compile("(\\d+)\\s*(화|회|편|話)");
    private static final Pattern ANY_NUM = Pattern.compile("(\\d+)");
    private static final Pattern SERIES = Pattern.compile(
            "^(.*?)\\s*(?:제?\\s*\\d+\\s*(?:화|회|편|話)|ep\\.?\\s*\\d+|#\\d+)", Pattern.CASE_INSENSITIVE);

    private final Context ctx;
    private final ContentResolver cr;
    private final SharedPreferences prefs;
    private volatile Map<String, Series> series = new LinkedHashMap<>();
    private volatile Map<String, Ep> eps = new LinkedHashMap<>();

    Library(Context ctx) {
        this.ctx = ctx;
        this.cr = ctx.getContentResolver();
        this.prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        loadCache();
    }

    // ------------------------------------------------------------ 폴더 목록
    List<String> roots() {
        List<String> out = new ArrayList<>();
        try {
            JSONArray a = new JSONArray(prefs.getString("roots", "[]"));
            for (int i = 0; i < a.length(); i++) out.add(a.getString(i));
        } catch (JSONException ignored) {
        }
        return out;
    }

    void addRoot(String uri) {
        List<String> r = roots();
        if (!r.contains(uri)) r.add(uri);
        prefs.edit().putString("roots", new JSONArray(r).toString()).apply();
    }

    void removeRoot(String uri) {
        List<String> r = roots();
        r.remove(uri);
        prefs.edit().putString("roots", new JSONArray(r).toString()).apply();
    }

    String rootsJson() {
        JSONArray a = new JSONArray();
        for (String r : roots()) {
            JSONObject o = new JSONObject();
            try {
                o.put("uri", r);
                o.put("name", prettyRoot(r));
            } catch (JSONException ignored) {
            }
            a.put(o);
        }
        return a.toString();
    }

    private static String prettyRoot(String uri) {
        try {
            String id = DocumentsContract.getTreeDocumentId(Uri.parse(uri));   // 예: primary:Download/Toon
            int c = id.indexOf(':');
            String path = c >= 0 ? id.substring(c + 1) : id;
            return path.isEmpty() ? "내장 저장공간" : path;
        } catch (Exception e) {
            return uri;
        }
    }

    // ------------------------------------------------------------ 검색
    private static class Child {
        String id, name, mime;
        long mtime;
    }

    private List<Child> list(Uri tree, String docId) {
        List<Child> out = new ArrayList<>();
        Uri ch = DocumentsContract.buildChildDocumentsUriUsingTree(tree, docId);
        String[] cols = {
                DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                DocumentsContract.Document.COLUMN_MIME_TYPE,
                DocumentsContract.Document.COLUMN_LAST_MODIFIED};
        try (Cursor c = cr.query(ch, cols, null, null, null)) {
            if (c == null) return out;
            while (c.moveToNext()) {
                Child k = new Child();
                k.id = c.getString(0);
                k.name = c.getString(1);
                k.mime = c.getString(2);
                k.mtime = c.isNull(3) ? 0 : c.getLong(3);
                if (k.name == null || k.name.startsWith(".")) continue;
                out.add(k);
            }
        } catch (Exception ignored) {
        }
        return out;
    }

    private static boolean isDir(Child k) {
        return DocumentsContract.Document.MIME_TYPE_DIR.equals(k.mime);
    }

    static boolean isImageName(String n) {
        String s = n.toLowerCase(Locale.ROOT);
        return s.endsWith(".jpg") || s.endsWith(".jpeg") || s.endsWith(".png") || s.endsWith(".webp")
                || s.endsWith(".gif") || s.endsWith(".avif") || s.endsWith(".bmp");
    }

    private static boolean isImage(Child k) {
        return (k.mime != null && k.mime.startsWith("image/")) || isImageName(k.name);
    }

    private static boolean isZip(Child k) {
        return k.name.toLowerCase(Locale.ROOT).endsWith(".zip");
    }

    synchronized void scan() {
        Map<String, Series> s = new LinkedHashMap<>();
        Map<String, Ep> e = new LinkedHashMap<>();
        for (String r : roots()) {
            try {
                Uri tree = Uri.parse(r);
                walk(tree, r, DocumentsContract.getTreeDocumentId(tree), "", 0, null, s, e);
            } catch (Exception ignored) {
            }
        }
        for (Series one : s.values()) {
            Collections.sort(one.eps, (a, b) -> a.num != b.num ? Integer.compare(a.num, b.num) : natural(a.name, b.name));
        }
        series = s;
        eps = e;
        saveCache();
    }

    private void walk(Uri tree, String rootKey, String docId, String name, int depth, String parentName,
                      Map<String, Series> s, Map<String, Ep> e) {
        List<Child> kids = list(tree, docId);
        List<Child> imgs = new ArrayList<>();
        long newest = 0;
        for (Child k : kids) {
            if (!isDir(k) && isImage(k)) imgs.add(k);
            newest = Math.max(newest, k.mtime);
        }

        // 이 폴더 자체가 한 화 (이미지가 바로 들어있음)
        if (depth > 0 && !imgs.isEmpty()) {
            Collections.sort(imgs, (a, b) -> natural(a.name, b.name));
            Ep ep = new Ep();
            ep.name = name;
            ep.uri = DocumentsContract.buildDocumentUriUsingTree(tree, docId).toString();
            ep.zip = false;
            for (Child k : imgs) ep.images.add(DocumentsContract.buildDocumentUriUsingTree(tree, k.id).toString());
            String sName = parentName != null ? parentName : seriesNameFrom(name);
            add(s, e, rootKey, sName, ep, newest);
        }

        // ZIP 파일들 = 한 화씩
        for (Child k : kids) {
            if (isDir(k) || !isZip(k)) continue;
            Ep ep = new Ep();
            ep.name = k.name.substring(0, k.name.length() - 4);
            ep.uri = DocumentsContract.buildDocumentUriUsingTree(tree, k.id).toString();
            ep.zip = true;
            String sName = depth > 0 ? name : seriesNameFrom(ep.name);
            add(s, e, rootKey, sName, ep, k.mtime);
        }

        if (depth < MAX_DEPTH) {
            for (Child k : kids) {
                if (isDir(k)) walk(tree, rootKey, k.id, k.name, depth + 1, depth > 0 ? name : null, s, e);
            }
        }
    }

    private void add(Map<String, Series> s, Map<String, Ep> e, String rootKey, String sName, Ep ep, long mtime) {
        String sid = hash(rootKey + "|" + sName);
        Series one = s.get(sid);
        if (one == null) {
            one = new Series();
            one.id = sid;
            one.name = sName;
            s.put(sid, one);
        }
        one.mtime = Math.max(one.mtime, mtime);
        ep.id = hash(ep.uri);
        ep.seriesId = sid;
        ep.num = epNumber(ep.name);
        one.eps.add(ep);
        e.put(ep.id, ep);
    }

    static int epNumber(String name) {
        Matcher m = EP_NUM.matcher(name);
        if (m.find()) return safeInt(m.group(1));
        m = ANY_NUM.matcher(name);
        return m.find() ? safeInt(m.group(1)) : 0;
    }

    private static int safeInt(String s) {
        try {
            return Integer.parseInt(s.length() > 9 ? s.substring(s.length() - 9) : s);
        } catch (NumberFormatException ex) {
            return 0;
        }
    }

    static String seriesNameFrom(String title) {
        String t = title == null ? "" : title.trim();
        t = t.replaceFirst("^\\d{3,}\\s+", "");  // "001 작품 1화" → "작품 1화"
        Matcher m = SERIES.matcher(t);
        String n = (m.find() && !m.group(1).trim().isEmpty()) ? m.group(1) : t;
        n = n.split("\\s[-|:]\\s")[0].trim();
        return n.isEmpty() ? "웹툰" : n;
    }

    static int natural(String a, String b) {
        Matcher ma = Pattern.compile("\\d+|\\D+").matcher(a), mb = Pattern.compile("\\d+|\\D+").matcher(b);
        while (true) {
            boolean fa = ma.find(), fb = mb.find();
            if (!fa || !fb) return Boolean.compare(fa, fb);
            String x = ma.group(), y = mb.group();
            int c;
            if (Character.isDigit(x.charAt(0)) && Character.isDigit(y.charAt(0))) {
                String xs = x.replaceFirst("^0+(?=.)", ""), ys = y.replaceFirst("^0+(?=.)", "");
                c = xs.length() != ys.length() ? Integer.compare(xs.length(), ys.length()) : xs.compareTo(ys);
            } else {
                c = x.compareToIgnoreCase(y);
            }
            if (c != 0) return c;
        }
    }

    static String hash(String s) {
        try {
            byte[] d = MessageDigest.getInstance("MD5").digest(s.getBytes(StandardCharsets.UTF_8));
            StringBuilder b = new StringBuilder();
            for (int i = 0; i < 6; i++) b.append(String.format("%02x", d[i]));
            return b.toString();
        } catch (Exception e) {
            return Integer.toHexString(s.hashCode());
        }
    }

    // ------------------------------------------------------------ 캐시 (앱을 열 때 바로 보이도록)
    private File cacheFile() {
        return new File(ctx.getFilesDir(), "library.json");
    }

    private void saveCache() {
        try {
            JSONArray a = new JSONArray();
            for (Series one : series.values()) {
                JSONObject o = new JSONObject();
                o.put("id", one.id).put("name", one.name).put("mtime", one.mtime);
                JSONArray ea = new JSONArray();
                for (Ep ep : one.eps) {
                    JSONObject x = new JSONObject();
                    x.put("id", ep.id).put("name", ep.name).put("uri", ep.uri).put("zip", ep.zip).put("num", ep.num);
                    x.put("images", new JSONArray(ep.images));
                    ea.put(x);
                }
                o.put("eps", ea);
                a.put(o);
            }
            try (OutputStream out = new FileOutputStream(cacheFile())) {
                out.write(a.toString().getBytes(StandardCharsets.UTF_8));
            }
        } catch (Exception ignored) {
        }
    }

    private void loadCache() {
        File f = cacheFile();
        if (!f.exists()) return;
        try (InputStream in = new FileInputStream(f)) {
            JSONArray a = new JSONArray(new String(readAll(in), StandardCharsets.UTF_8));
            Map<String, Series> s = new LinkedHashMap<>();
            Map<String, Ep> e = new LinkedHashMap<>();
            for (int i = 0; i < a.length(); i++) {
                JSONObject o = a.getJSONObject(i);
                Series one = new Series();
                one.id = o.getString("id");
                one.name = o.getString("name");
                one.mtime = o.optLong("mtime");
                JSONArray ea = o.getJSONArray("eps");
                for (int j = 0; j < ea.length(); j++) {
                    JSONObject x = ea.getJSONObject(j);
                    Ep ep = new Ep();
                    ep.id = x.getString("id");
                    ep.name = x.getString("name");
                    ep.uri = x.getString("uri");
                    ep.zip = x.getBoolean("zip");
                    ep.num = x.getInt("num");
                    ep.seriesId = one.id;
                    JSONArray im = x.getJSONArray("images");
                    for (int k = 0; k < im.length(); k++) ep.images.add(im.getString(k));
                    one.eps.add(ep);
                    e.put(ep.id, ep);
                }
                s.put(one.id, one);
            }
            series = s;
            eps = e;
        } catch (Exception ignored) {
        }
    }

    boolean hasCache() {
        return !series.isEmpty();
    }

    // ------------------------------------------------------------ JSON
    String libraryJson() throws JSONException {
        JSONArray a = new JSONArray();
        for (Series one : series.values()) {
            JSONObject o = new JSONObject();
            o.put("id", one.id).put("name", one.name).put("count", one.eps.size()).put("mtime", one.mtime);
            JSONArray ids = new JSONArray();
            for (Ep ep : one.eps) ids.put(ep.id);
            o.put("eps", ids);
            a.put(o);
        }
        return new JSONObject().put("series", a).put("roots", roots().size()).toString();
    }

    String seriesJson(String id) throws JSONException {
        Series one = series.get(id);
        if (one == null) return "null";
        JSONObject o = new JSONObject();
        o.put("id", one.id).put("name", one.name);
        JSONArray ea = new JSONArray();
        for (Ep ep : one.eps) {
            ea.put(new JSONObject().put("id", ep.id).put("name", ep.name).put("num", ep.num));
        }
        return o.put("eps", ea).toString();
    }

    String episodeJson(String id) throws Exception {
        Ep ep = eps.get(id);
        if (ep == null) return "null";
        Series one = series.get(ep.seriesId);
        int count = ep.zip ? extracted(ep).size() : ep.images.size();
        int idx = one.eps.indexOf(ep);
        JSONObject o = new JSONObject();
        o.put("id", ep.id).put("name", ep.name).put("num", ep.num).put("count", count)
                .put("seriesId", one.id).put("seriesName", one.name)
                .put("index", idx).put("total", one.eps.size())
                .put("prev", idx > 0 ? one.eps.get(idx - 1).id : JSONObject.NULL)
                .put("next", idx < one.eps.size() - 1 ? one.eps.get(idx + 1).id : JSONObject.NULL);
        return o.toString();
    }

    // ------------------------------------------------------------ 이미지
    static class Img {
        InputStream in;
        String mime;

        Img(InputStream in, String name) {
            this.in = in;
            this.mime = mimeOf(name);
        }
    }

    static String mimeOf(String name) {
        String s = name.toLowerCase(Locale.ROOT);
        if (s.endsWith(".png")) return "image/png";
        if (s.endsWith(".webp")) return "image/webp";
        if (s.endsWith(".gif")) return "image/gif";
        if (s.endsWith(".avif")) return "image/avif";
        if (s.endsWith(".bmp")) return "image/bmp";
        return "image/jpeg";
    }

    Img image(String epId, int i) throws Exception {
        Ep ep = eps.get(epId);
        if (ep == null) return null;
        if (ep.zip) {
            List<File> files = extracted(ep);
            if (i < 0 || i >= files.size()) return null;
            File f = files.get(i);
            return new Img(new FileInputStream(f), f.getName());
        }
        if (i < 0 || i >= ep.images.size()) return null;
        String u = ep.images.get(i);
        return new Img(cr.openInputStream(Uri.parse(u)), Uri.decode(u));
    }

    /** ZIP 화는 앱 임시 폴더에 풀어두고 사용 (최근 몇 화만 보관). */
    private List<File> extracted(Ep ep) throws IOException {
        File base = new File(ctx.getCacheDir(), "zips");
        File dir = new File(base, ep.id);
        File done = new File(dir, ".done");
        synchronized (this) {
            if (!done.exists()) {
                deleteTree(dir);
                File tmp = new File(base, ep.id + ".tmp");
                deleteTree(tmp);
                if (!tmp.mkdirs() && !tmp.isDirectory()) throw new IOException("임시 폴더를 만들 수 없음");
                List<String[]> got = new ArrayList<>();   // {원래 이름, 임시 파일 이름}
                try (ZipInputStream zin = new ZipInputStream(cr.openInputStream(Uri.parse(ep.uri)), StandardCharsets.UTF_8)) {
                    ZipEntry z;
                    while ((z = zin.getNextEntry()) != null) {
                        if (z.isDirectory()) continue;
                        String nm = z.getName();
                        nm = nm.substring(nm.lastIndexOf('/') + 1);
                        if (!isImageName(nm) || nm.startsWith(".")) continue;
                        String part = "p" + got.size();
                        try (OutputStream out = new FileOutputStream(new File(tmp, part))) {
                            copy(zin, out);
                        }
                        got.add(new String[]{nm, part});
                    }
                }
                if (got.isEmpty()) throw new IOException("ZIP 안에 이미지가 없음");
                // 원래 파일 이름 기준으로 정렬해서 00001.jpg, 00002.jpg ... 로 저장
                Collections.sort(got, (a, b) -> natural(a[0], b[0]));
                for (int i = 0; i < got.size(); i++) {
                    String nm = got.get(i)[0];
                    String ext = nm.substring(nm.lastIndexOf('.')).toLowerCase(Locale.ROOT);
                    File to = new File(tmp, String.format(Locale.ROOT, "%05d%s", i + 1, ext));
                    if (!new File(tmp, got.get(i)[1]).renameTo(to)) throw new IOException("이름 변경 실패");
                }
                if (!tmp.renameTo(dir)) throw new IOException("임시 폴더 이동 실패");
                new FileOutputStream(done).close();
                trimExtracted(base);
            }
            dir.setLastModified(System.currentTimeMillis());
        }
        File[] fs = dir.listFiles((d, n) -> isImageName(n));
        List<File> list = fs == null ? new ArrayList<>() : new ArrayList<>(Arrays.asList(fs));
        Collections.sort(list, (a, b) -> natural(a.getName(), b.getName()));
        return list;
    }

    private void trimExtracted(File base) {
        File[] dirs = base.listFiles(File::isDirectory);
        if (dirs == null || dirs.length <= KEEP_EXTRACTED) return;
        Arrays.sort(dirs, Comparator.comparingLong(File::lastModified));
        for (int i = 0; i < dirs.length - KEEP_EXTRACTED; i++) deleteTree(dirs[i]);
    }

    private static void deleteTree(File f) {
        if (f == null || !f.exists()) return;
        File[] kids = f.listFiles();
        if (kids != null) for (File k : kids) deleteTree(k);
        //noinspection ResultOfMethodCallIgnored
        f.delete();
    }

    // ------------------------------------------------------------ 표지 (첫 화 첫 장의 윗부분)
    File cover(String sid) {
        Series one = series.get(sid);
        if (one == null || one.eps.isEmpty()) return null;
        File dir = new File(ctx.getCacheDir(), "covers");
        File f = new File(dir, sid + ".jpg");
        if (f.exists()) return f;
        //noinspection ResultOfMethodCallIgnored
        dir.mkdirs();
        try {
            byte[] bytes = firstImageBytes(one.eps.get(0));
            if (bytes == null) return null;
            Bitmap bm = makeCover(bytes);
            if (bm == null) return null;
            File tmp = new File(dir, sid + ".tmp");
            try (OutputStream out = new FileOutputStream(tmp)) {
                bm.compress(Bitmap.CompressFormat.JPEG, 82, out);
            }
            bm.recycle();
            return tmp.renameTo(f) ? f : null;
        } catch (Exception e) {
            return null;
        }
    }

    private byte[] firstImageBytes(Ep ep) throws IOException {
        if (!ep.zip) {
            if (ep.images.isEmpty()) return null;
            try (InputStream in = cr.openInputStream(Uri.parse(ep.images.get(0)))) {
                return in == null ? null : readAll(in);
            }
        }
        File done = new File(new File(new File(ctx.getCacheDir(), "zips"), ep.id), ".done");
        if (done.exists()) {
            List<File> files = extracted(ep);
            if (!files.isEmpty()) try (InputStream in = new FileInputStream(files.get(0))) {
                return readAll(in);
            }
        }
        // 압축을 풀지 않고 ZIP에서 첫 이미지만 읽기
        try (ZipInputStream zin = new ZipInputStream(cr.openInputStream(Uri.parse(ep.uri)), StandardCharsets.UTF_8)) {
            ZipEntry z;
            while ((z = zin.getNextEntry()) != null) {
                if (!z.isDirectory() && isImageName(z.getName())) return readAll(zin);
            }
        }
        return null;
    }

    private static Bitmap makeCover(byte[] bytes) {
        BitmapFactory.Options o = new BitmapFactory.Options();
        o.inJustDecodeBounds = true;
        BitmapFactory.decodeByteArray(bytes, 0, bytes.length, o);
        int w = o.outWidth, h = o.outHeight;
        if (w <= 0 || h <= 0) return null;
        int sample = 1;
        while (w / (sample * 2) >= 360) sample *= 2;
        BitmapFactory.Options d = new BitmapFactory.Options();
        d.inSampleSize = sample;
        int cropH = Math.min(h, w * 4 / 3);   // 세로로 긴 웹툰 이미지는 윗부분만 (3:4)
        try {
            BitmapRegionDecoder r = BitmapRegionDecoder.newInstance(bytes, 0, bytes.length, false);
            Bitmap bm = r.decodeRegion(new Rect(0, 0, w, cropH), d);
            r.recycle();
            if (bm != null) return bm;
        } catch (Exception ignored) {
        }
        Bitmap full = BitmapFactory.decodeByteArray(bytes, 0, bytes.length, d);
        if (full == null) return null;
        int ch = Math.min(full.getHeight(), full.getWidth() * 4 / 3);
        Bitmap bm = Bitmap.createBitmap(full, 0, 0, full.getWidth(), ch);
        if (bm != full) full.recycle();
        return bm;
    }

    // ------------------------------------------------------------ 유틸
    static byte[] readAll(InputStream in) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        copy(in, b);
        return b.toByteArray();
    }

    static void copy(InputStream in, OutputStream out) throws IOException {
        byte[] buf = new byte[64 * 1024];
        int n;
        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
    }
}
