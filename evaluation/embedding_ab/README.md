# Dense embedding A/B experiment

Production model/config/data are not modified by this experiment. Only
`scripts/compare_embeddings.py`, this directory's score artifacts and an isolated
`data/chroma_embedding_test/ab_20260929/` workspace are used.

## Controls

- Original `evaluation/questions.json`: unchanged 25 questions (21 answerable, 4 no-answer).
- Extraction/OCR/cleaning/chunking run once with the production MiniLM pipeline.
- Seven evaluation chunks and six user-PDF chunks are frozen before encoding.
- Every model receives identical chunk text/metadata and questions; Top-K remains 4.
- The three user-PDF probes are a separate supplemental corpus; they are not added
  to the 25-question dataset and are not used to select the threshold.
- Per-model/per-corpus Chroma DBs live only under the experiment workspace.
- Production workspace is mounted read-only in the benchmark containers.
- Same CPU settings: 4 CPU threads, batch size 4, float32, normalized dense vectors.
- BGE-M3 uses no query instruction; E5, if tested, uses `query: ` and `passage: `.
- No reranking, sparse embeddings, hybrid retrieval, or generation in A/B.

The frozen snapshot may contain private PDF text. It stays under the excluded
`data/` directory, never here. JSON result artifacts contain scores/ranks/metadata,
not chunk text; the PDF filename is represented as `user_document.pdf`.

## Metrics

`best_relevant_score` is the highest score in Top-K with matching expected
source/location and expected keyword(s), using the existing evaluation matcher.
`top_score` is the highest candidate score; for no-answer cases it measures the
risk of accepting unsupported context. A retrieval false positive does not by
itself prove Gemini would generate a false answer.

Each artifact records unfiltered Top-K, scores, the unchanged baseline threshold
0.55, relevant/no-answer quantiles and their gap (minimum relevant minus maximum
no-answer). Threshold calibration occurs only after the model run. The 0.01 grid
maximizes recall subject to zero false positives on the four calibration negatives.
MiniLM keeps 0.55 when it is equally good. These are calibration results on a small
dataset, not independent validation or proof of future accuracy.

Input fingerprints and chunk fingerprints must match between models. Token limits
are checked per model without rewriting the frozen text. Any inherited MiniLM
retokenization/truncation is explicitly recorded rather than silently changing
the baseline; a new model that truncates the frozen chunks aborts the comparison.

## Timing and memory

Model download/cache-check time is separate from local cached model load time.
Query latency measures individual encoding calls, excluding Chroma search.
Warm document throughput is the median of three encodes of the same 13 chunks.
RSS includes Python/framework memory; the peak covers the whole process, including
download/load. Measurements are a single CPU run on this machine, not a load test.

Official usage references:

- [BAAI BGE-M3 model card](https://huggingface.co/BAAI/bge-m3)
- [Multilingual E5 base model card](https://huggingface.co/intfloat/multilingual-e5-base)

## Reproduction

Use a new experiment directory for a new frozen snapshot. From the repository root:

```powershell
New-Item -ItemType Directory -Force data/chroma_embedding_test/repeat,evaluation/embedding_ab_repeat
docker run --rm --memory=5g --cpus=4 -e HF_HOME=/cache/huggingface -e PADDLE_PDX_CACHE_HOME=/cache/paddlex -e PADDLE_HOME=/cache/paddle -v bai1-rag_model_cache:/cache -v "${PWD}:/workspace:ro" -v "${PWD}/data/chroma_embedding_test:/experiment" -w /workspace bai1-rag:local python -m scripts.compare_embeddings prepare --work /experiment/repeat --pdf /workspace/data/uploads/5a6a363f94d947a5ad17a17389f47508.pdf
docker run --rm --memory=5g --cpus=4 -e HF_HOME=/cache/huggingface -v bai1-rag_model_cache:/cache -v "${PWD}:/workspace:ro" -v "${PWD}/data/chroma_embedding_test:/experiment" -v "${PWD}/evaluation/embedding_ab_repeat:/results" -w /workspace bai1-rag:local python -m scripts.compare_embeddings run --work /experiment/repeat --results /results --model current
```

Run the same last command sequentially with `--model bge-m3`; use `e5-base` only
if the fallback is justified. Each invocation creates a new test DB, retaining old
attempts; score JSON for that model is the latest completed run. Do not rebuild or
reconfigure production to run this experiment.
