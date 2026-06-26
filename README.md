# Camille Elisa KB Pipeline

Cleans and formats Shopify product exports for the Camille Elisa AI chatbot knowledge base.  

---

## How to Run

### Step 1 — Export products from Shopify
In your Shopify admin, go to **Products → Export → Export all products (CSV)**.

### Step 2 — Upload the CSV to this repository
Go to the `input/` folder in this repository, click **Add file → Upload files**, and upload the CSV file you just exported.

### Step 3 — Run the pipeline
1. Click the **Actions** tab at the top of this repository
2. Select **Clean Shopify Products** from the left sidebar
3. Click **Run workflow**
4. Type in the name of the CSV file you uploaded (e.g. `products_export_1.csv`)
5. Click the green **Run workflow** button

### Step 4 — Download the output
1. Wait about a minute for the run to finish (a green checkmark will appear)
2. Click "Summary" on the left side (or go back to the Actions tab and select the most recent run)
3. Scroll to the bottom of the page, find the "Artifacts" section and download `products-output.zip`

### Step 5 — Import into Dify
1. Unzip the downloaded file
2. Open the `batches/` folder — you will see files named `products_batch_001.txt`, `products_batch_002.txt`, etc.
3. In Dify, go to your knowledge base and delete the previously imported batch files
4. Upload the new batch files one at a time, waiting for each one to finish indexing before uploading the next

---

## Files in This Repository

| File | Description |
|------|-------------|
| `clean_shopify_products.py` | Main cleaning script |
| `requirements.txt` | Python dependencies |
| `.github/workflows/run_pipeline.yml` | GitHub Actions workflow |
| `input/` | Place your Shopify CSV exports here |

---

## Output Files

After running the pipeline, the output artifact contains:

| File | Description |
|------|-------------|
| `batches/products_batch_001.txt` ... | Files ready to import into Dify, split into batches of 40 products to avoid rate limits |
| `products_knowledge_base.txt` | All products in a single file (for reference) |
| `products_chunks.json` | Structured JSON data for each product chunk |
| `review_flags.json` | Products with many variants whose pricing was auto-summarized — worth a quick check |

---

## Notes

- Draft and unpublished products are automatically excluded
- Products with many size/carat combinations are summarized as a price range instead of listed individually — check `review_flags.json` after each run to verify these look correct
- Import batch files one at a time with a short wait between each, to avoid hitting the embedding rate limit in Dify
