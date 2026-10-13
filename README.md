# Lab media robot

Busca todos los días noticias, entrevistas y comunicados públicos sobre Constanza Baquedano, decide si de verdad son sobre ella y los guarda en `news.json`. La sección "In the media" de la web del lab en Wix lee ese archivo.

**100% gratis:** no usa llaves, cuentas externas ni tarjetas. Corre en GitHub Actions, que es gratis para repositorios públicos.

## Dónde busca

- Google News, en español (Chile) y en inglés (EE.UU.)
- El tema de Google News de ella
- Bing News
- GDELT, un índice mundial de noticias
- Sus canales propios, si los agregas (canal de YouTube o cualquier feed RSS del lab)

## Cómo sabe quién es ella

Antes de buscar arma un **perfil de identidad** (`identity.json`) que se reconstruye en cada vuelta:

1. **Sus publicaciones en OpenAlex**: coautores, instituciones, temas y títulos de sus papers.
2. **Palabras semilla** en `config.json`: afiliaciones (UAI, CSCN, MIDAP, Mind & Life, Lyon...) y temas (meditación, naturaleza, neurofenomenología...). Más palabras **negativas** que restan puntos, como Plaza Baquedano o General Baquedano.
3. **Lo que aprende de ti**: cuando apruebas o bloqueas cosas, ajusta qué palabras indican que es ella. Nunca aprende en contra de su nombre ni de las semillas.
4. **IA open-source gratis**: un modelo multilingüe (`paraphrase-multilingual-MiniLM-L12-v2`) que corre dentro de GitHub, sin cuenta ni llave. Compara el significado de cada nota con los resúmenes de sus papers y con lo que tú aprobaste, versus ejemplos de "no es ella" (Plaza Baquedano, el General, deportes) y lo que bloqueaste. Si algún día no carga, el robot sigue funcionando solo con las reglas.

Cada nota recibe un puntaje con el detalle del porqué (lo ves en `REVIEW.md`).

## Qué se publica solo

- Puntaje alto → se publica solo en la web.
- Dudoso → queda en `REVIEW.md` y te llega un correo de GitHub.
- Homónimos claros, cosas anteriores a 2015 o repetidas → se ignoran.
- Nunca borra nada: lo que encuentra queda guardado para siempre.

## Lo que haces tú (solo cuando te llega un aviso)

Todo en `config.json`, con el lápiz de editar en GitHub:

- **Mostrar** algo que quedó en revisión: copia su "approve line" de `REVIEW.md` dentro de `"approve": [ ]`.
- **Ocultar para siempre** (aunque ya esté publicado): pega esa línea, o parte del título, medio o link, en `"blocklist": [ ]`. El robot además aprende de esto.
- **Canales propios**: si ella o el lab tienen canal de YouTube, pon su ID en `youtube_channel_ids`. Lo que suban se publica solo, con reproductor en la web.
- Guardas ("Commit changes") y el robot se vuelve a correr en un par de minutos.

Ojo: el último elemento de cada lista va **sin** coma al final.

## Correrlo a mano

Pestaña **Actions** → **Find news** → **Run workflow**.

## Lo que no hace (y por qué)

Instagram, LinkedIn, X, TikTok, Reddit, Bluesky y la búsqueda de YouTube no tienen ninguna forma gratis y sin cuenta de buscar contenido de terceros, así que esa parte no está. Lo que sí sirve: agregar los canales propios de ella o del lab en `own_channels`.
