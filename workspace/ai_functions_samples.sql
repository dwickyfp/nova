-- ============================================================================
-- Nova AI Functions - Sample Usage SQL
-- ============================================================================
-- This file demonstrates all 7 native AI functions available in Nova.
-- These functions are SQL UDF wrappers around StarRocks' built-in ai_query()
-- function, allowing LLM-powered analytics directly from SQL.
--
-- PREREQUISITES:
--   1. StarRocks 4.1+ (ai_query() builtin)
--   2. AI Provider configured in NOVA_SYSTEM.CONFIG_AI_PROVIDERS
--   3. AI Model configured in NOVA_SYSTEM.CONFIG_AI_MODELS
--   4. Function aliases configured in NOVA_SYSTEM.CONFIG_MODEL_ALIASES
--   5. UDFs auto-registered on backend startup (or via API)
--
-- HOW IT WORKS:
--   - Each AI function is a SQL UDF that wraps ai_query(prompt, config_json)
--   - The config_json contains: model, api_key, endpoint_url
--   - If no alias is configured, functions return a helpful error message
--   - When configured, functions call the LLM and return the response
--
-- API ENDPOINTS for management:
--   GET    /api/v1/ai/aliases              - List all aliases
--   POST   /api/v1/ai/aliases              - Create alias
--   PUT    /api/v1/ai/aliases/{id}         - Update alias
--   DELETE /api/v1/ai/aliases/{id}         - Delete alias
--   POST   /api/v1/ai/aliases/register-udfs - Re-register all UDFs
--   GET    /api/v1/ai/aliases/udf-status   - Check UDF registration status
-- ============================================================================

-- ============================================================================
-- SECTION 0: Verify UDFs are registered
-- ============================================================================
SHOW GLOBAL FUNCTIONS;
-- Expected: ai_classify, ai_complete, ai_extract, ai_filter,
--           ai_sentiment, ai_summarize, ai_translate


-- ============================================================================
-- SECTION 1: AI_COMPLETE - General LLM Completion
-- ============================================================================
-- Signature: AI_COMPLETE(prompt STRING) -> STRING
-- Description: Send a prompt to the LLM and get a completion response.
-- Use case: Text generation, Q&A, code generation, explanations.

-- 1a. Simple text generation
SELECT AI_COMPLETE('Write a haiku about databases') AS haiku;

-- 1b. Generate SQL query from natural language
SELECT AI_COMPLETE(
    'Write a StarRocks SQL query to find the top 5 customers by total order amount. Only return the SQL, no explanation.'
) AS generated_sql;

-- 1c. Generate product description
SELECT 
    p.product_name,
    p.category,
    AI_COMPLETE(
        CONCAT('Write a compelling 2-sentence marketing description for a product called "', 
               p.product_name, '" in the category "', p.category, '".')
    ) AS marketing_description
FROM NOVA_EXAMPLE.products p
LIMIT 3;

-- 1d. Explain data anomalies
SELECT AI_COMPLETE(
    CONCAT('Explain why this order might have a very high total amount: $', 
           CAST(total_amount AS STRING), 
           ' for order ID ', CAST(order_id AS STRING),
           '. Status: ', status)
) AS explanation
FROM NOVA_EXAMPLE.orders
ORDER BY total_amount DESC
LIMIT 1;


-- ============================================================================
-- SECTION 2: AI_SENTIMENT - Sentiment Analysis
-- ============================================================================
-- Signature: AI_SENTIMENT(txt STRING) -> STRING
-- Description: Analyze the sentiment of text. Returns JSON with sentiment
--              (positive/negative/neutral/mixed) and confidence score.
-- Use case: Customer feedback, review analysis, social media monitoring.

-- 2a. Single text sentiment
SELECT AI_SENTIMENT('I absolutely love this product! Best purchase ever!') AS sentiment;

-- 2b. Batch sentiment analysis on customer data
SELECT 
    c.first_name,
    c.last_name,
    AI_SENTIMENT(
        CONCAT('I had a ', 
               CASE WHEN o.status = 'DELIVERED' THEN 'great' ELSE 'terrible' END,
               ' experience with my order #', CAST(o.order_id AS STRING),
               '. The total was $', CAST(o.total_amount AS STRING), '.')
    ) AS sentiment
FROM NOVA_EXAMPLE.orders o
JOIN NOVA_EXAMPLE.customers c ON o.customer_id = c.customer_id
LIMIT 5;

-- 2c. Sentiment classification with CASE
SELECT 
    sentiment_raw,
    CASE 
        WHEN sentiment_raw LIKE '%positive%' THEN 'POSITIVE'
        WHEN sentiment_raw LIKE '%negative%' THEN 'NEGATIVE'
        WHEN sentiment_raw LIKE '%neutral%' THEN 'NEUTRAL'
        ELSE 'UNKNOWN'
    END AS sentiment_label
FROM (
    SELECT AI_SENTIMENT('The product is okay, nothing special but works fine.') AS sentiment_raw
) t;


-- ============================================================================
-- SECTION 3: AI_CLASSIFY - Zero-Shot Classification
-- ============================================================================
-- Signature: AI_CLASSIFY(txt STRING, categories STRING) -> STRING
-- Description: Classify text into one of the provided categories.
--              Returns ONLY the category name.
-- Use case: Ticket routing, content categorization, intent detection.

-- 3a. Simple classification
SELECT AI_CLASSIFY(
    'The stock market crashed today, wiping out billions in value',
    'finance, sports, technology, politics, entertainment'
) AS category;

-- 3b. Classify products into custom categories
SELECT 
    p.product_name,
    p.category AS original_category,
    AI_CLASSIFY(
        p.product_name,
        'electronics, clothing, food, furniture, books, other'
    ) AS ai_category
FROM NOVA_EXAMPLE.products p
LIMIT 5;

-- 3c. Classify orders by value tier
SELECT 
    o.order_id,
    o.total_amount,
    AI_CLASSIFY(
        CONCAT('Order total is $', CAST(o.total_amount AS STRING), 
               ' with status ', o.status),
        'low value, medium value, high value, enterprise'
    ) AS value_tier
FROM NOVA_EXAMPLE.orders o
LIMIT 5;

-- 3d. Customer segment classification
SELECT 
    c.first_name,
    c.country,
    AI_CLASSIFY(
        CONCAT('Customer from ', c.country, ' who joined on ', 
               CAST(c.created_at AS STRING)),
        'domestic, international, new market, established market'
    ) AS market_segment
FROM NOVA_EXAMPLE.customers c
LIMIT 5;


-- ============================================================================
-- SECTION 4: AI_SUMMARIZE - Text Summarization
-- ============================================================================
-- Signature: AI_SUMMARIZE(txt STRING) -> STRING
-- Description: Summarize text concisely in 2-3 sentences.
-- Use case: Long text condensation, report summaries, content digest.

-- 4a. Simple summarization
SELECT AI_SUMMARIZE(
    'StarRocks is a high-performance analytical database that delivers sub-second queries on real-time data. It uses a massively parallel processing architecture and supports both tabular and semi-structured data. The system is designed for scenarios requiring high concurrency and low latency, making it ideal for dashboards, ad-hoc analytics, and real-time decision making. StarRocks also supports materialized views, external catalogs, and integrates with popular BI tools.'
) AS summary;

-- 4b. Summarize order information
SELECT 
    o.order_id,
    AI_SUMMARIZE(
        CONCAT('Order #', CAST(o.order_id AS STRING),
               ' was placed on ', CAST(o.order_date AS STRING),
               ' with a total of $', CAST(o.total_amount AS STRING),
               '. Current status: ', o.status,
               '. Shipping to: ', COALESCE(o.shipping_address, 'N/A'))
    ) AS order_summary
FROM NOVA_EXAMPLE.orders o
LIMIT 3;

-- 4c. Summarize customer profile
SELECT 
    c.customer_id,
    c.first_name,
    AI_SUMMARIZE(
        CONCAT(c.first_name, ' ', c.last_name,
               ' is a customer from ', COALESCE(c.city, 'unknown city'), 
               ', ', COALESCE(c.country, 'unknown country'),
               '. Email: ', COALESCE(c.email, 'N/A'),
               '. Phone: ', COALESCE(c.phone, 'N/A'),
               '. Member since ', CAST(c.created_at AS STRING))
    ) AS customer_summary
FROM NOVA_EXAMPLE.customers c
LIMIT 3;


-- ============================================================================
-- SECTION 5: AI_EXTRACT - Entity Extraction
-- ============================================================================
-- Signature: AI_EXTRACT(txt STRING, json_schema STRING) -> STRING
-- Description: Extract structured information from text. Returns JSON
--              matching the provided schema.
-- Use case: Data parsing, form extraction, unstructured-to-structured conversion.

-- 5a. Extract person information
SELECT AI_EXTRACT(
    'John Doe is a 30-year-old software engineer living in Jakarta. His email is john.doe@example.com and phone is +628****7890.',
    'name, age, occupation, city, email, phone'
) AS extracted_info;

-- 5b. Extract order details from free text
SELECT AI_EXTRACT(
    'Customer ordered 5 units of Widget Pro at $29.99 each, shipping to 123 Main St, Springfield. Expected delivery next Tuesday.',
    'product_name, quantity, unit_price, shipping_address, delivery_date'
) AS order_details;

-- 5c. Extract from shipping address
SELECT 
    o.order_id,
    AI_EXTRACT(
        COALESCE(o.shipping_address, 'No address provided'),
        'street_address, city, state, postal_code, country'
    ) AS parsed_address
FROM NOVA_EXAMPLE.orders o
WHERE o.shipping_address IS NOT NULL
LIMIT 3;

-- 5d. Extract key information from customer records
SELECT 
    c.customer_id,
    AI_EXTRACT(
        CONCAT('Name: ', c.first_name, ' ', c.last_name,
               ', Email: ', COALESCE(c.email, 'N/A'),
               ', Phone: ', COALESCE(c.phone, 'N/A'),
               ', Location: ', COALESCE(c.city, ''), ', ', COALESCE(c.country, '')),
        'full_name, email, phone, city, country'
    ) AS customer_info
FROM NOVA_EXAMPLE.customers c
LIMIT 3;


-- ============================================================================
-- SECTION 6: AI_TRANSLATE - Text Translation
-- ============================================================================
-- Signature: AI_TRANSLATE(txt STRING, target_lang STRING) -> STRING
-- Description: Translate text to the target language. Returns ONLY the
--              translation, no explanation.
-- Use case: Multi-language support, localization, cross-language search.

-- 6a. Simple translation
SELECT AI_TRANSLATE('Hello, how are you today?', 'Indonesian') AS translation;

-- 6b. Translate to multiple languages using UNION
SELECT 'English' AS language, 'Welcome to Nova Analytics Platform' AS text
UNION ALL
SELECT 'Indonesian', AI_TRANSLATE('Welcome to Nova Analytics Platform', 'Indonesian')
UNION ALL
SELECT 'Japanese', AI_TRANSLATE('Welcome to Nova Analytics Platform', 'Japanese')
UNION ALL
SELECT 'Spanish', AI_TRANSLATE('Welcome to Nova Analytics Platform', 'Spanish')
UNION ALL
SELECT 'French', AI_TRANSLATE('Welcome to Nova Analytics Platform', 'French');

-- 6c. Translate product names
SELECT 
    p.product_name AS original_name,
    AI_TRANSLATE(p.product_name, 'Indonesian') AS indonesian_name,
    AI_TRANSLATE(p.product_name, 'Japanese') AS japanese_name
FROM NOVA_EXAMPLE.products p
LIMIT 3;

-- 6d. Translate customer city/country for international report
SELECT 
    c.first_name,
    c.country AS original_country,
    AI_TRANSLATE(COALESCE(c.country, 'Unknown'), 'English') AS country_en
FROM NOVA_EXAMPLE.customers c
LIMIT 5;


-- ============================================================================
-- SECTION 7: AI_FILTER - Semantic Boolean Filter
-- ============================================================================
-- Signature: AI_FILTER(txt STRING, criteria STRING) -> STRING
-- Description: Check if text matches a semantic criteria. Returns 'true' or 'false'.
-- Use case: Content moderation, relevance filtering, semantic search.

-- 7a. Simple semantic filter
SELECT AI_FILTER(
    'The new iPhone 15 features an improved camera and faster processor',
    'is about mobile phones or smartphones'
) AS is_about_phones;

-- 7b. Filter using WHERE clause
-- (Note: This calls the LLM for each row, which may be slow/expensive)
SELECT 
    p.product_name,
    p.category
FROM NOVA_EXAMPLE.products p
WHERE AI_FILTER(
    CONCAT(p.product_name, ' in category ', p.category),
    'is related to electronics or technology'
) = 'true'
LIMIT 5;

-- 7c. Combine AI_FILTER with other AI functions
SELECT 
    p.product_name,
    AI_SUMMARIZE(
        AI_COMPLETE(
            CONCAT('Describe the product "', p.product_name, '" in 2 sentences.')
        )
    ) AS product_summary
FROM NOVA_EXAMPLE.products p
WHERE AI_FILTER(p.product_name, 'sounds like a premium or luxury product') = 'true'
LIMIT 3;

-- 7d. Order relevance filtering
SELECT 
    o.order_id,
    o.total_amount,
    o.status
FROM NOVA_EXAMPLE.orders o
WHERE AI_FILTER(
    CONCAT('Order with total $', CAST(o.total_amount AS STRING), ' and status ', o.status),
    'is a high-value order that might need special attention'
) = 'true'
ORDER BY o.total_amount DESC
LIMIT 5;


-- ============================================================================
-- SECTION 8: Advanced Patterns - Combining AI Functions
-- ============================================================================

-- 8a. Multi-step AI pipeline: Extract -> Classify -> Sentiment
SELECT 
    original_text,
    extracted_json,
    AI_CLASSIFY(extracted_json, 'personal, business, technical, other') AS classification,
    AI_SENTIMENT(original_text) AS sentiment
FROM (
    SELECT 
        'Meeting scheduled with John Smith from TechCorp on 2024-01-15 to discuss the new API integration project. He seemed very enthusiastic about the timeline.' AS original_text,
        AI_EXTRACT(
            'Meeting scheduled with John Smith from TechCorp on 2024-01-15 to discuss the new API integration project. He seemed very enthusiastic about the timeline.',
            'person_name, company, date, topic, sentiment'
        ) AS extracted_json
) t;

-- 8b. Batch processing with INSERT INTO SELECT
-- (Persist AI results to a table for caching and performance)
/*
CREATE TABLE IF NOT EXISTS NOVA_EXAMPLE.product_ai_enriched AS
SELECT 
    p.product_id,
    p.product_name,
    p.category,
    p.price,
    AI_COMPLETE(
        CONCAT('Write a 1-sentence description for: ', p.product_name)
    ) AS ai_description,
    AI_CLASSIFY(
        p.product_name,
        'premium, budget, mid-range, luxury, utility'
    ) AS price_tier,
    AI_SENTIMENT(p.product_name) AS name_sentiment
FROM NOVA_EXAMPLE.products p
WHERE p.product_id <= 5;
*/

-- 8c. Using AI functions in subqueries
SELECT 
    category,
    COUNT(*) AS product_count,
    AVG(price) AS avg_price,
    AI_SUMMARIZE(
        CONCAT('Category "', category, '" has ', CAST(COUNT(*) AS STRING),
               ' products with an average price of $', 
               CAST(ROUND(AVG(price), 2) AS STRING))
    ) AS category_summary
FROM NOVA_EXAMPLE.products
GROUP BY category
ORDER BY product_count DESC;

-- 8d. Real-time customer insight generation
SELECT 
    c.customer_id,
    c.first_name,
    c.last_name,
    c.country,
    order_stats.total_orders,
    order_stats.total_spent,
    AI_COMPLETE(
        CONCAT('Write a brief customer insight for ', c.first_name, ' ', c.last_name,
               ' from ', COALESCE(c.country, 'unknown'),
               ' who has placed ', CAST(order_stats.total_orders AS STRING),
               ' orders totaling $', CAST(order_stats.total_spent AS STRING),
               '. Categorize them as a customer segment.')
    ) AS customer_insight
FROM NOVA_EXAMPLE.customers c
JOIN (
    SELECT 
        customer_id,
        COUNT(*) AS total_orders,
        SUM(total_amount) AS total_spent
    FROM NOVA_EXAMPLE.orders
    GROUP BY customer_id
) order_stats ON c.customer_id = order_stats.customer_id
LIMIT 3;


-- ============================================================================
-- SECTION 9: Manual UDF Registration (if auto-registration fails)
-- ============================================================================
-- If UDFs are not auto-registered by the backend, you can register them
-- manually. Replace the config JSON with your actual provider details.

-- 9a. Register AI_SENTIMENT manually (example)
/*
DROP GLOBAL FUNCTION IF EXISTS AI_SENTIMENT(STRING);

CREATE GLOBAL FUNCTION AI_SENTIMENT(txt STRING)
RETURNS ai_query(
    CONCAT('Analyze the sentiment of the following text. Reply with JSON: {"sentiment": "positive|negative|neutral|mixed", "confidence": 0.0-1.0}\n\nText: ', txt),
    '{"model": "claude-opus-4-8", "api_key": "YOUR_API_KEY", "endpoint_url": "http://host.docker.internal:20128/v1"}'
);
*/

-- 9b. Register AI_COMPLETE manually (example)
/*
DROP GLOBAL FUNCTION IF EXISTS AI_COMPLETE(STRING);

CREATE GLOBAL FUNCTION AI_COMPLETE(prompt STRING)
RETURNS ai_query(
    prompt,
    '{"model": "claude-opus-4-8", "api_key": "YOUR_API_KEY", "endpoint_url": "http://host.docker.internal:20128/v1"}'
);
*/

-- 9c. Register all 7 functions via backend API
/*
-- Using curl to trigger re-registration:
curl -X POST http://localhost:8420/api/v1/ai/aliases/register-udfs

-- Check registration status:
curl http://localhost:8420/api/v1/ai/aliases/udf-status
*/


-- ============================================================================
-- SECTION 10: Performance Tips & Best Practices
-- ============================================================================
--
-- 1. USE AI_FILTER FIRST:
--    Apply AI_FILTER to narrow down rows before calling heavier functions.
--    This reduces the number of LLM calls significantly.
--
--    GOOD:
--    SELECT AI_SUMMARIZE(text) FROM articles 
--    WHERE AI_FILTER(text, 'is about technology') = 'true';
--
--    BAD:
--    SELECT AI_SUMMARIZE(text) FROM articles WHERE category = 'tech';
--
-- 2. CACHE RESULTS WITH INSERT INTO SELECT:
--    LLM calls are expensive. Persist results to avoid re-computation.
--
--    INSERT INTO enriched_data
--    SELECT id, AI_SENTIMENT(text) AS sentiment FROM source_data;
--
-- 3. USE MATERIALIZED VIEWS:
--    Create MVs with AI results that refresh periodically.
--
-- 4. LIMIT ROWS:
--    Always use LIMIT when testing to avoid unexpected costs.
--
-- 5. BATCH SIMILAR QUERIES:
--    Instead of calling AI functions row-by-row in an app,
--    batch them in a single SQL query.
--
-- 6. MONITOR TOKEN USAGE:
--    Each AI function call consumes tokens. Monitor usage via:
--    SELECT * FROM NOVA_SYSTEM.USAGE_QUERY_STATS 
--    WHERE query LIKE '%ai_%' ORDER BY start_time DESC LIMIT 10;
--
-- 7. PREFER BUILT-IN AI FUNCTIONS:
--    Prefer higher-level functions (AI_SENTIMENT, AI_CLASSIFY) over
--    generic AI_COMPLETE for lower latency and cost.
--
-- ============================================================================

-- End of AI Functions Sample SQL
-- ============================================================================
