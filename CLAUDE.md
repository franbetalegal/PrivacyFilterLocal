# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Herramienta local de detección/redacción de PII en documentos (texto, PDF, DOCX). GitHub: `franbetalegal/PrivacyFilterLocal`.

- **Run backend:** `source .venv/bin/activate && python -m server.main` (uvicorn, puerto 7860 por defecto; `PF_HOST`/`PF_PORT` lo override)
- **Run frontend (dev):** `corepack pnpm -C frontend dev` (proxya `/api` a :7860)
- **Tests backend:** `source .venv/bin/activate && python -m pytest` (637 tests)
- **Tests/lint frontend:** `corepack pnpm -C frontend run test` / `lint` / `typecheck` (bootstrap con vitest + Testing Library + ESLint flat config, antes no existían)
- **Stack:** FastAPI (`server/`) + React/Vite/TS (`frontend/`). Modelo `opf` (PyTorch) + reconocedores ES deterministas (DNI/IBAN/etc. con checksum) + NER spaCy ES/CA, con pseudonimización y verificación anti-fuga post-redacción.
- **Arquitectura:** desde ago-2026 `server/main.py` es solo el ensamblador (app/middleware/lifespan/CLI, ~160 líneas); las rutas viven en `server/routes/{core,markdown,dictionary,dataset,updates,diagnostics}.py`, el registro de tokens de descarga en `server/downloads.py`, y el logging a fichero en `server/logging_setup.py`.
- **CI:** `.github/workflows/tests.yml` corre pytest + frontend lint/typecheck/test/build en cada push/PR a `main` (antes solo había CI en tags de release, vía `release.yml`).
- **Gotcha de CI:** `server/moe_fast.py` calibra con un benchmark real de CPU cacheado por host bajo el directorio del checkpoint del modelo; en runners efímeros de GitHub Actions nunca hay cache hit, así que el benchmark se re-ejecuta cada vez y puede colgar el job. Mitigado con `PF_FAST_MOE=0` en el job de CI más un `skipif(CI=="true")` en `tests/test_moe_fast.py::test_calibration_picks_the_faster_strategy_and_caches_it` (ese test llama al benchmark directamente, sin pasar por la env var).
- `requirements-server.txt` tiene versiones pinneadas exactas (antes no tenía ninguna) para builds reproducibles del portable.
- **Modelos NER (desde 2.7.0):** los modelos spaCy NO son dependencia pip ni van en ningún paquete. `server/ner_models.py` los descarga en `PF_NER_DIR` (por defecto `<carpeta de la app>/ner-models`, ~1,2 GB) en el primer arranque y comprueba versión en cada inicio contra la tabla de compatibilidad que publica spaCy. Un modelo instalado con `python -m spacy download` cuenta como presente y se usa tal cual, así que el flujo de desarrollo no cambia.
- **Dónde se verifica que el entorno está completo:** `server/inference.run_preflight()` (arranque, vía el lifespan de `server/main.py`) instala lo que falte; `inference.components()` alimenta `/api/health` y el bundle de diagnóstico, y `status()["ready"]` es la única definición de "la app puede anonimizar ahora" — el frontend la consume vía `isReady()`. Añadir una capa de detección implica añadirla a `components()`, o volverá a poder faltar en silencio.
- **Empaquetado (desde 2.8.0):** los tres paquetes traen Tesseract (binario + `spa`/`cat`/`eng` de `tessdata_fast`), y los de macOS y Linux traen además un CPython relocalizable (python-build-standalone), así que ninguna plataforma exige nada instalado en la máquina. Las versiones van pinneadas en el bloque `env` de `release.yml` y en la cabecera de `build_portable.ps1`. Los lanzadores anteponen `tesseract/bin` al `PATH` y exportan `PF_TESSDATA_DIR`; `server/pdf_ops.py` lo pasa como `--tessdata-dir` en vez de fiarse de `TESSDATA_PREFIX`, que cambió de significado entre Tesseract 3 y 4.
- **Guarda de release:** `smoke_ner.py` y `smoke_ocr.py` corren en el job `smoke` de `release.yml` en Windows, macOS y Linux; el primero hace la descarga de primer arranque y falla si un nombre en mayúsculas no se detecta; el segundo rasteriza una página sin capa de texto y falla si el OCR no la lee. El job depende de los tres builds porque prueba el Tesseract que se publica, no el de la máquina. Los tests de spaCy en `tests/test_ner_es.py` se saltan sin modelos, que es justo el estado roto, así que la cobertura real vive ahí.

## Convención de idiomas

**Backend en inglés, frontend en castellano.** Aplica a prosa e identificadores:

- `server/` y `tests/`: código, identificadores, comentarios, docstrings, mensajes de log y salida de CLI de desarrollo, todo en inglés.
- `frontend/src/`: todo el texto visible por el usuario en castellano. Los identificadores (componentes, props, tipos, clases CSS) siguen en inglés.

**El backend no emite prosa de usuario.** Emite un código y sus parámetros; el castellano lo pone el frontend. El contrato vive en `server/messages.py` y su contrapartida en `frontend/src/messages.ts`. Dos formas llegan al cliente: `warnings: [{code, params}]` en respuestas correctas, y `detail: {code, params}` en errores HTTP. Añadir un mensaje implica tocar ambos lados; un código sin traducción se muestra en crudo, a propósito, para que se note en revisión.

**Excepción: el castellano que es dato, no prosa, se queda.** No es inconsistencia — la herramienta detecta español, así que su conocimiento es español:

- Los léxicos de `server/lexicon_es.py` y los patrones de `server/recognizers_es.py`.
- Las etiquetas de entidad (`NOMBRE`, `DIRECCION`, `SEG_SOCIAL`): aparecen como marcadores `[NOMBRE_1]` **dentro del documento anonimizado**, que lo lee un cliente español. Traducirlas empeoraría el producto.
- El sufijo `_es` de los ficheros marca ámbito de idioma, no es prosa.
