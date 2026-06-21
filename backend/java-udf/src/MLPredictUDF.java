package com.nova.udf;

import java.io.OutputStream;
import java.io.InputStream;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * Nova ML_PREDICT Java UDF
 * Calls Nova backend internal endpoint to run sklearn ML prediction.
 *
 * Usage: SELECT ML_PREDICT('model_alias', '{"feature1": 1.0, "feature2": 2.0}')
 * Returns: prediction result as STRING (e.g., "COMPLETED" or "150.5")
 */
public class MLPredictUDF {

    // Nova backend URL - use host.docker.internal for Docker BE containers
    private static final String BACKEND_URL =
        System.getProperty("nova.backend.url",
            "http://host.docker.internal:8000/api/v1/internal/ml/predict");

    private static final int TIMEOUT_MS = 30000;

    public String evaluate(String modelAlias, String featuresJson) {
        if (modelAlias == null || featuresJson == null) {
            return null;
        }
        try {
            // Build request body
            String body = "{\"model_alias\":\"" + modelAlias.replace("\"", "\\\"") +
                          "\",\"features\":" + featuresJson + "}";

            // Call Nova internal endpoint
            URL url = new URL(BACKEND_URL);
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setConnectTimeout(TIMEOUT_MS);
            conn.setReadTimeout(TIMEOUT_MS);
            conn.setDoOutput(true);

            try (OutputStream os = conn.getOutputStream()) {
                os.write(body.getBytes(StandardCharsets.UTF_8));
            }

            int status = conn.getResponseCode();
            InputStream is = (status >= 200 && status < 300)
                ? conn.getInputStream()
                : conn.getErrorStream();

            StringBuilder sb = new StringBuilder();
            try (BufferedReader br = new BufferedReader(
                    new InputStreamReader(is, StandardCharsets.UTF_8))) {
                String line;
                while ((line = br.readLine()) != null) {
                    sb.append(line);
                }
            }

            if (status != 200) {
                return "ERROR: " + sb.toString();
            }

            // Parse prediction from JSON response
            // Response: {"model_alias":"...","model_name":"...","prediction":"COMPLETED",...}
            String resp = sb.toString();
            String pred = extractJsonField(resp, "prediction");
            return pred != null ? pred : "ERROR: could not parse prediction from: " + resp;

        } catch (Exception e) {
            return "ERROR: " + e.getMessage();
        }
    }

    /**
     * Simple JSON field extractor - avoids external dependencies.
     * Handles string, number, boolean values.
     */
    private String extractJsonField(String json, String field) {
        String key = "\"" + field + "\"";
        int idx = json.indexOf(key);
        if (idx < 0) return null;

        int colonIdx = json.indexOf(':', idx + key.length());
        if (colonIdx < 0) return null;

        // Skip whitespace
        int start = colonIdx + 1;
        while (start < json.length() && json.charAt(start) == ' ') start++;
        if (start >= json.length()) return null;

        char firstChar = json.charAt(start);

        if (firstChar == '"') {
            // String value
            int end = start + 1;
            while (end < json.length()) {
                if (json.charAt(end) == '"' && json.charAt(end - 1) != '\\') break;
                end++;
            }
            return json.substring(start + 1, end);
        } else if (firstChar == 'n') {
            // null
            return null;
        } else {
            // Number or boolean
            int end = start;
            while (end < json.length() &&
                   json.charAt(end) != ',' &&
                   json.charAt(end) != '}' &&
                   json.charAt(end) != ' ') {
                end++;
            }
            return json.substring(start, end).trim();
        }
    }
}
