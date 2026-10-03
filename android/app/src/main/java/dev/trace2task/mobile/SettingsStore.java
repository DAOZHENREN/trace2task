package dev.trace2task.mobile;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** User-owned API key encrypted with a non-exportable Android Keystore key. Backups disabled. */
final class SettingsStore {
    private static final String ALIAS = "trace2task-api-key-v1";
    private final SharedPreferences prefs;

    SettingsStore(Context context) { prefs = context.getSharedPreferences("cloud", Context.MODE_PRIVATE); }
    String endpoint() { return prefs.getString("endpoint", ""); }
    String model() { return prefs.getString("model", ""); }
    int limit() { return prefs.getInt("limit", 20); }

    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (store.containsAlias(ALIAS)) return (SecretKey) store.getKey(ALIAS, null);
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
        return generator.generateKey();
    }

    String apiKey() throws Exception {
        String encrypted = prefs.getString("encrypted_key", "");
        if (encrypted.isEmpty()) return "";
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        byte[] iv = Base64.decode(prefs.getString("iv", ""), Base64.NO_WRAP);
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, iv));
        return new String(cipher.doFinal(Base64.decode(encrypted, Base64.NO_WRAP)), StandardCharsets.UTF_8);
    }

    void save(String endpoint, String model, String apiKey, int limit) throws Exception {
        // Validate before changing any saved configuration.
        new CloudModelClient(endpoint, model, apiKey);
        if (limit < 1 || limit > 100) throw new IllegalArgumentException("步数必须为 1–100");
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key());
        String encrypted = Base64.encodeToString(cipher.doFinal(apiKey.trim().getBytes(StandardCharsets.UTF_8)), Base64.NO_WRAP);
        if (!prefs.edit().putString("endpoint", endpoint.trim()).putString("model", model.trim())
                .putInt("limit", limit).putString("encrypted_key", encrypted)
                .putString("iv", Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP)).commit())
            throw new IllegalStateException("配置保存失败");
    }

    void clear() { prefs.edit().clear().apply(); }
}
