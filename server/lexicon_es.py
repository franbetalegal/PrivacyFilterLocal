"""Spanish/Catalan legal-financial vocabulary used to reject NER false positives.

Kept in its own module because it's data, not logic: these lists grow every
time a new document type surfaces new noise, and mixing them into
:mod:`server.ner_es` made that file hard to read.

Two tiers matter:

``NEVER_IN_NAME``
    Words that never appear inside a real person / place name in these
    documents. A *single* occurrence anywhere in a span rejects the whole span.
    Safe because a name like "Juan García Pérez" contains none of them.

``GENERIC_SINGLE``
    Words that are noise *on their own* but legitimately appear inside real
    entities ("Plaza Mayor de Madrid", "Calle Comadrán"). Only reject when the
    span is that single word.

The remaining sets drive specific rules (verbs, numerals, street-type markers,
public institutions) — see :mod:`server.ner_es` for how each is applied.
"""
from __future__ import annotations

# --- Procedural / notarial verb forms -------------------------------------
# Imperative-subjunctive formulas ("Notifíquese", "Dése traslado") and the
# bare infinitives that appear in powers-of-attorney lists ("Dirigir",
# "Desistir", "Tachar testigos"). Never names.
VERBS = frozenset({
    "notifíquese", "notifiquese", "líbrese", "librese", "únase", "unase",
    "remítase", "remitase", "archívese", "archivese", "cúmplase", "cumplase",
    "hágase", "hagase", "practíquese", "practiquese", "procédase", "procedase",
    "regístrese", "registrese", "publíquese", "publiquese",
    "comuníquese", "comuniquese", "devuélvase", "devuelvase",
    "expídase", "expidase", "tráigase", "traigase",
    "dése", "dese", "déseles", "advierto", "resuelvo", "resolc", "dispongo",
    "certifico", "acuerdo", "acordar", "declaro", "manifiesto",
    "dirigir", "desistir", "intervenir", "prestar", "reconocer", "tachar",
    "renunciar", "transigir", "allanarse", "recurrir", "apelar", "impugnar",
    "solicitar", "otorgar", "comparecer", "ratificar", "aceptar", "requerir",
    "dejo", "doy", "firmado", "firmada", "signado", "signada", "sellado",
    "leído", "leido", "aprobado", "conforme",
})

# --- Contract / banking boilerplate --------------------------------------
# The bulk of the noise in bank policies and notarial deeds. Any of these
# inside a span means it's clause text, not an entity.
NEVER_IN_NAME = frozenset({
    # Tax and accounting vocabulary. These reach the analyzer as field labels in
    # AEAT forms, where a lone capitalised noun looks exactly like a first name.
    # Reviewed on a real 28-page liquidation proposal, every false positive the
    # reviewer flagged was one of these or a single-token variant of one.
    # Deliberately excludes words that are also Spanish surnames (Prada, Soler,
    # Bravo…); where a term below could be a rare surname, the context rescue in
    # ner_es keeps it when a person trigger precedes it.
    "contribuyente", "contribuyentes", "ganancia", "ganancias",
    "perdida", "pérdida", "perdidas", "pérdidas",
    "renta", "rentas", "dilacion", "dilación", "dilaciones",
    "donativo", "donativos", "minimo", "mínimo", "maximo", "máximo",
    "liquidable", "imponible", "retencion", "retención", "retenciones",
    "deduccion", "deducción", "deducciones", "cuota", "cuotas", "tramo",
    "autonomica", "autonómica", "autonomico", "autonómico", "estatal",
    "integracion", "integración", "compensacion", "compensación",
    "transmision", "transmisión", "transmisiones",
    "patrimonial", "patrimoniales", "usufructo", "nuda",
    "inmueble", "inmuebles", "ejercicio", "devengo", "borrador",
    "alegaciones", "gestora", "aaee",
    # Contract structure
    "contrato", "contrase", "poliza", "póliza", "escritura", "escrituras",
    "clausula", "cláusula", "clausulado", "estipulaciones", "condiciones",
    "condicion", "condición", "anexo", "anexos", "apartado", "apartados",
    "otorgamiento", "protocolo", "adenda", "adendas", "novacion", "novación",
    "renovacion", "renovación", "prorroga", "prórroga", "prorrogas",
    "declaracion", "declaración", "declaraciones", "certificado",
    "requerimiento", "notificacion", "notificación", "comunicacion",
    "comunicación", "advertencias", "informe", "solicitud", "convenio",
    # Banking / finance
    "banco", "bancos", "banca", "credito", "crédito", "creditos", "préstamo",
    "prestamo", "prestamos", "deuda", "deudas", "deudor", "deudora",
    "acreedor", "acreedores", "acreditado", "acreditada", "avalista", "aval",
    "avales", "garantia", "garantía", "garantias", "garante", "garantes",
    "interes", "interés", "intereses", "euribor", "tipo", "tipos",
    "comision", "comisión", "comisiones", "importe", "importes", "saldo",
    "saldos", "cuenta", "cuentas", "abono", "abonos", "adeudo", "cargo",
    "pago", "pagos", "cobro", "liquidacion", "liquidación", "liquidaciones",
    "amortizacion", "amortización", "vencimiento", "vencimientos",
    "disposicion", "disposición", "disponibilidad", "financiacion",
    "financiación", "financiaciones", "inversion", "inversión", "subvencion",
    "subvención", "subvenciones", "ayuda", "ayudas", "minimis", "aportacion",
    "capital", "circulante", "inmovilizado", "balance", "activo", "pasivo",
    "gasto", "gastos", "coste", "costes", "ingreso", "ingresos",
    "factura", "facturas", "nomina", "nóminas", "nominas", "dividendos",
    "moneda", "euros", "euro", "divisa", "iva", "impuesto", "impuestos",
    "tributaria", "tributarias", "seguro", "seguros", "renting", "leasing",
    "deposito", "depósito", "depositos", "fianza", "hipoteca", "hipotecario",
    "hipotecarios", "solvencia", "insolvencia", "concurso", "concursal",
    "morosidad", "demora", "incumplimiento", "excedido", "excedidos",
    "limite", "límite", "plazo", "plazos", "periodo", "período",
    "operacion", "operación", "operaciones", "contratacion", "contratación",
    # Corporate / registry
    "sociedad", "sociedades", "mercantil", "empresa", "empresas", "entidad",
    "entidades", "pyme", "pymes", "autonomo", "autónomo", "autonomos",
    "administrador", "administradora", "administradores", "apoderado",
    "apoderados", "consejero", "consejo", "junta", "socio", "socios",
    "accionista", "accionistas", "titular", "titulares", "titularidad",
    "representante", "representantes", "representacion", "representación",
    "cliente", "clientes", "interesado", "interesados", "beneficiario",
    "beneficiaria", "parte", "partes", "sucursal", "oficina", "oficinas",
    "grupo", "matriz", "filial", "unipersonal",
    # Institutions / administration
    "juzgado", "juzgados", "tribunal", "tribunales", "audiencia", "audiencias",
    "magistratura", "magistrado", "fiscalia", "fiscalía", "fiscal",
    "juez", "jueza", "jurado", "sindico", "síndicos", "sindicos",
    "notaria", "notaría", "notario", "notarial", "notariado", "escribano",
    "registro", "registrador", "registradores", "registral", "registrales",
    "hacienda", "agencia", "ministerio", "ministeri", "conselleria",
    "consejeria", "consejería", "generalitat", "diputacion", "diputación",
    "ayuntamiento", "administracion", "administración", "administraciones",
    "publica", "pública", "publicas", "públicas", "organismo", "organismos",
    "delegacion", "delegación", "departamento", "departament", "servicio",
    "servicios", "servei", "unitat", "unidad", "seccion", "sección", "secció",
    "deganat", "decanato", "colegio", "colegios", "collegi", "col·legi",
    "comunidad", "autonoma", "autónoma", "provincia", "municipio", "estado",
    "estat", "gobierno", "govern", "parlamento", "parlament", "comision",
    "comisión", "aduanas", "instituto", "institut", "central", "territorial",
    "seguridad", "social", "sanidad", "trabajo",
    # Law references
    "ley", "leyes", "llei", "lleis", "decreto", "legislativo", "legislacion",
    "legislación", "reglamento", "reglamentos", "directiva", "articulo",
    "artículo", "article", "art", "codigo", "código", "constitucion",
    "constitución", "boe", "dogc", "sentencia", "sentència", "auto",
    "resolucion", "resolución", "providencia", "diligencia", "diligencias",
    "recurso", "recursos", "casacion", "casación", "apelacion", "apelación",
    "demanda", "demandante", "demandada", "demandado", "demandat",
    "denuncia", "denunciante", "querella", "acusacion", "acusación",
    "procedimiento", "procediment", "expediente", "expedient", "autos",
    "instruccion", "instrucción", "instrucció", "enjuiciamiento",
    "enjuiciamento", "lec", "lecrim", "lopj", "lsc", "rgpd", "lgt",
    "testifical", "testigo", "testigos", "perito", "peritos", "pericial",
    "letrado", "letrada", "abogado", "abogada", "advocat", "advocada",
    "procurador", "procuradora", "abogacia", "abogacía",
    "derecho", "derechos", "dret", "obligacion", "obligación",
    "obligaciones", "responsabilidad", "responsabilidades", "facultad",
    "facultades", "poder", "poderes", "judicial", "civil", "penal",
    "mercantil", "laboral", "contencioso", "jurisdiccion", "jurisdicció",
    "nulidad", "nul·litat", "prescripcion", "prescripción",
    # Data protection boilerplate
    "datos", "dato", "dades", "proteccion", "protección", "personales",
    "personal", "tratamiento", "consentimiento", "finalidad", "finalidades",
    "conservacion", "conservación", "cesion", "cesión", "destinatarios",
    "portabilidad", "oposicion", "oposición", "supresion", "supresión",
    "limitacion", "limitación", "anonimizada", "confidencial",
    "confidencialidad",
    # Property description (not identifiers — those get their own recognizer)
    
    
    "construccion", "construcción", "obra", "obras", 
    "edificacion", "edificación", 
    "comunidad", 
    
    
    "superficie", "linderos", "linda", "lindante", "cabida",
    "mobiliario", "maquinaria", "instalacion", "instalación", "alarma",
    "software", "hardware", "elementos", "propiedad", "propiedades",
    "transporte", "terrestre", "adquisicion", "adquisición",
    # Compass / orientation
    "norte", "sur", "este", "oeste", "noreste", "noroeste", "sureste",
    "suroeste", "levante", "poniente", "mediodia", "mediodía", "frente",
    "fondo", "izquierda", "derecha", "izq", "dcha",
    # Generic document / form labels
    "pagina", "página", "pàgina", "folio", "foli", "tomo", "libro", "llibre",
    "hoja", "hojas", "linea", "línea", "lineas", "inscripcion", "inscripción",
    "referencia", "referencias", "numero", "número", "núm", "num",
    "fecha", "fechas", "hora", "data", "lugar", "domicilio", "domicili",
    "direccion", "dirección", "adreça", "adreca", "telefono", "teléfono",
    "tel", "tlf", "telf", "tfno", "fax", "correo", "email", "web",
    "codigo", "codi", "postal", "provincia", "pais", "país", "nacionalidad",
    "residencia", "nombre", "nom", "apellidos", "apellido", "razon", "raó",
    "documento", "documentos", "document", "documents", "identificacion",
    "identificación", "identificació", "identidad", "nacional", "nif", "cif",
    "dni", "nie", "nass", "hash", "algorisme", "algoritmo", "verificacion",
    "verificació", "verificación", "segur", "signatura", "firma", "firmante",
    "sello", "diligencia", "copia", "original", "adjunto", "adjuntos",
    "annexats", "annexat", "dades", "sol·licitud", "sollicitud",
    "importe", "total", "subtotal", "concepto", "conceptos", "detalle",
    "observaciones", "otros", "otras", "varios", "varias",
    # Time / calendar words that only appear in clauses
    "dias", "días", "dia", "día", "mes", "meses", "año", "años", "anos",
    "trimestre", "trimestres", "semestre", "natural", "naturales",
    "habil", "hábil", "habiles", "hábiles", "inhabil", "inhábil",
    "vigor", "vigente", "vigencia",
})

# --- Words that are noise alone but fine inside a real entity -------------
GENERIC_SINGLE = frozenset({
    "calle", "carrer", "avenida", "avinguda", "plaza", "plaça", "paseo",
    "passeig", "carretera", "camino", "cami", "camí", "ronda", "travesia",
    "travesía", "rambla", "via", "vía", "glorieta", "callejon", "callejón",
    "centro", "barrio", "distrito", "sector", "zona", "cerro", "monte",
    "serrat", "partida", "finca", "fincas", "manso", "casa", "chalet",
    "torre", "torres", "palacio", "iglesia", "ermita",
    "señor", "señora", "senyor", "senyora", "sr", "sra", "srta",
    "don", "doña", "dona", "en", "na",
    "tipus", "enviament", "rut", "doc", "organo", "órgano", "organ",
    "trámite", "trámit", "tramit", "tipo", "clase", "classe", "modalidad",
    "importe", "cantidad", "cuantia", "cuantía", "saldo",
    "confidentia", "confidencial", "borrador", "copia", "anverso", "reverso",
    # Address components: noise alone, valid inside a real address.
    "aparcamiento",
    "atico",
    "bloque",
    "catastral",
    "complejo",
    "duplex",
    "dúplex",
    "edificio",
    "entresuelo",
    "escalera",
    "heredad",
    "industrial",
    "local",
    "locales",
    "masia",
    "nave",
    "naves",
    "parcela",
    "parcelas",
    "parking",
    "planta",
    "poligono",
    "polígono",
    "portal",
    "puerta",
    "registral",
    "registrales",
    "rustica",
    "rústica",
    "sa",
    "sl",
    "slu",
    "solar",
    "sotano",
    "suelo",
    "sótano",
    "terreno",
    "terrenos",
    "trastero",
    "urbana",
    "urbanas",
    "urbanizacion",
    "urbanización",
    "urbano",
    "urbanos",
    "vivienda",
    "viviendas",
    "ático",
})

# --- Public institutions and regions (multi-word phrases) ----------------
# Not personal data: redacting them destroys context without protecting a
# natural person. Matched after whitespace/punctuation normalisation.
PUBLIC_PHRASES = frozenset(p.lower() for p in {
    "administracion de justicia", "administración de justicia",
    "administracio de justicia", "administració de justícia",
    "ministerio fiscal", "ministerio de justicia", "poder judicial",
    "consejo general del poder judicial", "consejo general del notariado",
    "consejo general de procuradores", "consejo general de la abogacia",
    "parlamento europeo", "consejo europeo", "comision europea",
    "union europea", "unió europea", "banco de espana", "banco de españa",
    "banco central europeo", "tribunal supremo", "tribunal constitucional",
    "tribunal de cuentas", "tribunal de la competencia",
    "audiencia nacional", "audiencia provincial", "cortes generales",
    "agencia tributaria", "agencia estatal de administracion tributaria",
    "agencia espanola de proteccion de datos",
    "agencia española de protección de datos",
    "seguridad social", "tesoreria general de la seguridad social",
    "instituto de credito oficial", "instituto de crédito oficial",
    "registro mercantil", "registro de la propiedad", "registro publico",
    "registro civil", "direccion general de aduanas",
    "direccion general de los registros y del notariado",
    "dirección general de los registros y del notariado",
    "direccion general de seguridad juridica",
    "dirección general de seguridad jurídica",
    "boletin oficial del estado", "boletín oficial del estado",
    "generalitat de catalunya", "parlament de catalunya",
    "colegio notarial de cataluna", "colegio notarial de cataluña",
    "colegio notarial de catalunya", "col·legi notarial de catalunya",
    "administraciones publicas", "administraciones públicas",
    "administracions publiques", "administració pública",
    "codigo civil", "código civil", "codigo penal", "código penal",
    "codigo de comercio", "código de comercio", "constitucion espanola",
    "ley de enjuiciamiento civil", "ley de enjuiciamiento criminal",
    "ley concursal", "ley hipotecaria", "ley general tributaria",
    "base de datos", "titulares reales", "base de datos de titulares reales",
    "fe publica", "fe pública", "asistencia juridica gratuita",
    "asistencia jurídica gratuita", "oficina judicial",
    "codi segur de verificacio", "codi segur de verificació",
    "algorisme hash", "signatura electronica", "signatura electrònica",
})

# --- Autonomous communities and provinces ---------------------------------
# A closed, stable set (17 + 2 + 50) that is never personal data. Kept here so
# a bare region name is rejected even when the source label is unknown;
# *municipalities* are handled by the label-driven bare-toponym rule instead,
# because there are 8000+ of them and no list would stay current.
REGIONS = frozenset({
    # Autonomous communities and cities
    "andalucia", "andalucía", "aragon", "aragón", "asturias", "baleares",
    "illes balears", "canarias", "cantabria", "castilla y leon",
    "castilla y león", "castilla-la mancha", "cataluna", "cataluña",
    "catalunya", "extremadura", "galicia", "la rioja", "madrid",
    "comunidad de madrid", "murcia", "region de murcia", "navarra",
    "pais vasco", "país vasco", "euskadi", "comunidad valenciana",
    "valencia", "ceuta", "melilla", "espana", "españa", "espanya",
    # Provinces not already covered above
    "alava", "álava", "albacete", "alicante", "almeria", "almería", "avila",
    "ávila", "badajoz", "barcelona", "burgos", "caceres", "cáceres", "cadiz",
    "cádiz", "castellon", "castellón", "ciudad real", "cordoba", "córdoba",
    "cuenca", "girona", "gerona", "granada", "guadalajara", "guipuzcoa",
    "guipúzcoa", "huelva", "huesca", "jaen", "jaén", "leon", "león",
    "lleida", "lerida", "lérida", "lugo", "malaga", "málaga", "ourense",
    "orense", "palencia", "las palmas", "pontevedra", "salamanca",
    "santa cruz de tenerife", "segovia", "sevilla", "soria", "tarragona",
    "teruel", "toledo", "valladolid", "vizcaya", "bizkaia", "zamora",
    "zaragoza",
})

# --- Street-type markers: presence means "this is an address" ------------
# Used to tell a real address ("Calle Comadrán 5") from a bare municipality
# ("Barcelona"), which is not personal data on its own.
STREET_MARKERS = frozenset({
    "calle", "c/", "cl", "carrer", "avenida", "avda", "avda.", "av", "av.",
    "avinguda", "plaza", "pza", "pza.", "plza", "plaça", "paseo", "pº",
    "passeig", "carretera", "ctra", "ctra.", "camino", "cami", "camí",
    "ronda", "travesia", "travesía", "rambla", "glorieta", "callejon",
    "poligono", "polígono", "urbanizacion", "urbanización", "urbanitzacio",
    "barrio", "bloque", "portal", "escalera", "piso", "puerta", "nave",
    "parcela", "km", "s/n", "numero", "número", "núm", "nº", "n°",
})

# --- Spanish number words (written-out numerals) -------------------------
NUMBER_WORDS = frozenset({
    "cero", "un", "uno", "una", "dos", "tres", "cuatro", "cinco", "seis",
    "siete", "ocho", "nueve", "diez", "once", "doce", "trece", "catorce",
    "quince", "dieciseis", "dieciséis", "diecisiete", "dieciocho",
    "diecinueve", "veinte", "veintiuno", "veintiuna", "veintidos",
    "veintidós", "veintitres", "veintitrés", "veinticuatro", "veinticinco",
    "veintiseis", "veintiséis", "veintisiete", "veintiocho", "veintinueve",
    "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta",
    "noventa", "cien", "ciento", "cientos", "doscientos", "trescientos",
    "cuatrocientos", "quinientos", "seiscientos", "setecientos",
    "ochocientos", "novecientos", "mil", "miles", "millon", "millón",
    "millones", "primero", "primera", "segundo", "segunda", "tercero",
    "tercera", "cuarto", "cuarta", "quinto", "quinta", "sexto", "sexta",
    "septimo", "séptimo", "octavo", "noveno", "decimo", "décimo",
    "undecimo", "duodecimo", "duodécima", "decimocuarta", "decimoquinta",
    "ultimo", "último", "ultima", "última",
})

# Month names — a lone month is not personal data.
MONTHS = frozenset({
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "setiembre", "octubre", "noviembre", "diciembre",
    "gener", "febrer", "març", "marc", "maig", "juny", "juliol", "agost",
    "setembre", "octubre", "novembre", "desembre",
})

# --- context triggers -----------------------------------------------------
#
# The lexicons above answer "does this span look like boilerplate?" using the
# span alone. That is blunt in one specific way: NEVER_IN_NAME rejects a whole
# span when any single token hits it, and "Banco", "Construcción", "Palencia"
# and "Contrato" are real Spanish surnames. Measured on the synthetic corpus,
# that rule alone accounted for four of six missed names.
#
# The lexicons below answer a different question — "does the text immediately
# BEFORE this span announce a person?" — so a span can be rescued from the
# blunt rejection without weakening the structural rules that catch OCR
# garbage, amounts and statute citations.
#
# Entries are stored unaccented and lowercase; matching folds accents, so
# "Doña" reaches "dona" and "compareció" reaches "comparecio".

# Single tokens that only ever introduce a natural person. Deliberately
# excludes ambiguous words ("persona", "firma", "titular", "cita"), which are
# covered as multi-word phrases below — "la firma de la empresa" must not
# rescue a company name.
PERSON_TRIGGER_WORDS = frozenset({
    # Courtesy titles.
    "d", "da", "dna", "don", "dona", "sr", "sra", "sres", "sras", "srta",
    # Procedural and contractual roles.
    "compareciente", "comparecientes", "interesado", "interesada",
    "demandante", "demandado", "demandada", "codemandado", "codemandada",
    "denunciante", "denunciado", "denunciada", "querellante",
    "testigo", "testigos", "letrado", "letrada", "procurador", "procuradora",
    "abogado", "abogada", "perito", "peritos", "notario", "notaria",
    "fiador", "fiadora", "avalista", "arrendador", "arrendadora",
    "arrendatario", "arrendataria", "prestatario", "prestataria",
    "prestamista", "comprador", "compradora", "vendedor", "vendedora",
    "apoderado", "apoderada", "representado", "representada",
    "heredero", "heredera", "legatario", "legataria", "causante",
    "solicitante", "beneficiario", "beneficiaria", "adjudicatario",
    "trabajador", "trabajadora", "empleado", "empleada",
    "conyuge", "conyuges", "otorgante", "otorgantes",
    # Verbs of appearance (finite forms only — the infinitive is too generic).
    "comparece", "comparecio", "comparecen", "interviene", "intervienen",
    "declara", "manifiesta", "manifiestan", "suscribe", "suscriben",
    "otorga", "otorgan", "acepta", "reconoce",
})

# Multi-word triggers, for cases where the individual words are too generic to
# be safe on their own.
PERSON_TRIGGER_PHRASES = frozenset({
    "se persona", "se personan", "en nombre de", "en nombre propio",
    "en representacion de", "representado por", "representada por",
    "a favor de", "por parte de",
    "firma en prueba", "firman en prueba", "firmado por",
    "con dni", "con nie", "con nif", "con pasaporte", "provisto de",
    "provista de", "domiciliado en", "domiciliada en",
    "nacido el", "nacida el", "mayor de edad",
    "comparecencia de", "se cita a", "se emplaza a",
    "doy fe de que", "identificado como", "identificada como",
})

# NOT a trigger: "declaracion de". In Spanish tax and legal documents it
# introduces a thing far more often than a person ("declaración de la renta",
# "declaración de bienes"), and the whole point of the rescue is to stop
# manufacturing false positives.

# The subset safe to peel off the FRONT of a span (see
# ``ner_es.strip_leading_person_trigger``). Restricted to phrases that
# unambiguously announce a natural person, because anything peeled here turns
# the remainder into a name candidate.
PERSON_TRIGGER_PHRASES_LEADING = frozenset({
    "comparecencia de", "se cita a", "se emplaza a",
    "en nombre de", "representado por", "representada por",
    "a favor de", "identificado como", "identificada como",
})

# Legal-entity markers. Policy decision: company names stay in the clear so the
# document keeps making sense, so a context trigger must NOT rescue a span that
# carries one of these. A sole trader is the exception the guard has to respect
# — there the "company name" is a natural person's name and carries no marker.
ENTITY_MARKERS = frozenset({
    "sl", "sa", "slu", "sau", "slp", "slne", "scp", "sc", "scoop", "coop",
    "sci", "aie", "ute", "cb", "sicav", "sgr", "fp",
    "sociedad", "sociedades", "cooperativa", "asociacion", "fundacion",
    "mercantil", "limitada", "anonima", "unipersonal",
    "gmbh", "ltd", "llc", "inc", "plc", "bv", "nv", "spa", "srl", "sas",
})
