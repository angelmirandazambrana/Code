# Esquema de carpetas de la biblioteca (propuesta)

Borrador para revisar. Nada se mueve hasta que el esquema esté decidido y probado en un juego.

## Por qué

El clasificador automático acierta bien el **juego** y bastante bien la **edición**, pero falla en la
**subcarpeta**, porque cada juego está organizado con un criterio distinto. Con 1.500 libros de
prueba, entre un 17% y un 36% de los libros acababan en una carpeta hermana o en otra rama del
mismo juego. Con un árbol coherente, clasificar deja de ser «adivinar una carpeta entre 13.664» y
pasa a ser responder tres preguntas: **qué juego, qué edición y qué tipo de libro**, que es justo lo
que los métodos automáticos (y Claude) hacen bien.

## Cómo está hoy (noviembre de 2026)

- ~100.000 documentos en **13.664 carpetas**; **3.564 carpetas tienen un solo fichero** y 8.388 tienen tres o menos.
- Profundidad muy variable: hay documentos de 2 a 11 niveles; el grueso entre 3 y 6.
- **Cada juego con su propio orden:**

| Juego | Ahora | Orden de los niveles |
|---|---|---|
| D&D | `RPG/D&D/Adventures/5e (WotC, 2014)/Adventure League/S9 - …` | tipo → edición → serie |
| D&D de terceros | `RPG/D&D Third Party/5e (2014)/Kobold Press/…` | edición → editorial |
| Pathfinder | `RPG/Paizo/1st Edition/…` | editorial → edición |
| Shadowrun, WFRP | `RPG/Shadowrun/5e (Catalyst, 2013)/…` | edición (bien) |
| Traveller | `RPG/Traveller/01 - Classic Traveller/…` | edición numerada |
| Call of Cthulhu | `RPG/Call of Cthulhu/Cthulhu Now`, `…/7e (Chaosium, 2014)` | mezcla época y edición |
| Warhammer 40K | `Wargames/Miniaturas/Games Workshop/Warhammer 40K/9e (2020)/01) Rulebooks & Missions` | formato → editorial → juego → edición → tipo numerado |
| OSR | `RPG/OSR(ic)/[1e Compatible, OSRIC]/…`, `RPG/OSR(ic)/Labyrinth Lord` | familia → juego o tema |

- Los **tipos** se llaman de muchas formas: `Rulebooks`, `Core`, `Rules`, `01) Rulebooks & Missions`,
  `Adventures`, `Modules`, `Adventure Paths`, `Settings`, `Players Options`, `Bestiaries`, `Codex & Index`,
  `Errata & FAQs`, `Misc`, `_Varios`…
- Lo que ya está bien: las ediciones siguen casi siempre el formato **`Ne (Editorial, año)`** de la fase 2,
  y las revistas están juntas en `Magazines/<Revista>`.

## Esquema decidido

```
RPG/<Game>/<Edition>/<Type>/[<Series>]/[<Language>]/fichero
Wargames/<Publisher>/<Game>/<Edition>/<Type>/[<Series>]/[<Language>]/fichero
```

Decisiones tomadas (noviembre de 2026):

- **Tipos en inglés**, con una lista cerrada (abajo).
- **Las familias de juegos (OSR, PbtA, Fate, World of Darkness…) no son un nivel**: son una etiqueta del catálogo para buscar y filtrar.
- **D&D de terceros es un juego propio**: `RPG/D&D 3rd Party/<Edition>/…`, al mismo nivel que `RPG/D&D`. La editorial de cada libro la guarda el catálogo (filtro «Editorial»), no la ruta.
- **Wargames: el primer nivel es la editorial**: `Wargames/Games Workshop/Warhammer 40K/…`. El formato (miniaturas, tablero, solitario) deja de ser carpeta y pasa a ser un dato del catálogo.
- **Siempre hay carpeta de edición**, también en los juegos con una sola: `1e (Editorial, año)`. Lo que vale para cualquier edición: `Any Edition`.
- **Libros traducidos**: en una carpeta de idioma junto al original (`…/Core Rules/Spanish/`).
- **Prueba con un juego que no sea el más grande**: Warhammer 40K (unos 1.550 documentos en 165 carpetas, con tipos muy mal nombrados).
- Pendiente: ambientaciones que cruzan ediciones (por ahora, repartidas por edición).

### Área y juego

| Área | Qué va |
|---|---|
| `RPG` | Juegos de rol: `RPG/<Game>` |
| `Wargames` | `Wargames/<Publisher>/<Game>` |
| `Juegos de mesa` | Juegos de mesa |
| `Magazines` | Revistas y fanzines (todas, también las de un solo juego) |
| `assets` | Recursos: mapas, tokens, papercraft, música, módulos de VTT |

El **juego** es el producto con reglas propias y su nombre más conocido: `D&D`, `D&D 3rd Party`,
`Pathfinder`, `Starfinder`, `Shadowrun`, `Call of Cthulhu`, `Trail of Cthulhu`, `Warhammer 40K`, `Necromunda`…

### Edición

Formato **`Ne (Editorial, año)`**: `5e (Wizards of the Coast, 2014)`, `9e (Games Workshop, 2020)`,
`1e (Paizo, 2009)`. Para lo que sirve en cualquier edición: `Any Edition`.

### Tipos (lista cerrada)

**Rol**

| Type | Qué va | Hoy se llama… |
|---|---|---|
| `Core Rules` | Reglas básicas, manual del jugador/máster, kit de inicio | Rulebooks, Core, Core Books, Rules |
| `Supplements` | Opciones de reglas, clases, bestiarios, equipo | Players Options, Bestiaries |
| `Settings` | Mundos y regiones, con una subcarpeta por mundo | Settings |
| `Adventures` | Módulos, aventuras, campañas, adventure paths | Adventures, Modules, Adventure Paths |
| `Organized Play` | Adventure League, Living Greyhawk, Pathfinder Society…, por programa y temporada | Adventure League, Society |
| `Game Aids` | Pantallas, fichas, cartas, mapas del producto | Character Sheets, Cards, Screens |
| `Fan & Homebrew` | Material no oficial | Homebrew, Unofficial |
| `Fiction` | Novelas, relatos, cómics | Novels |
| `Background` | Libros de trasfondo sin reglas | Background, Lore |
| `Misc` | Lo que no encaja (mejor vacío) | Misc, _Varios |

**Wargames**

| Type | Qué va | Hoy se llama… |
|---|---|---|
| `Rules` | Reglas básicas, puntos | Rulebooks & Missions, Rule Book |
| `Army Books` | Codex, índices, listas de ejército, army books | Codex & Index, Codices, Army Lists |
| `Scenarios & Campaigns` | Misiones, campañas, war zones | Campaigns & Expansions, Campaigns |
| `Errata & FAQ` | Erratas, FAQ, Big FAQ | Errata & FAQs, FAQs |
| `Supplements` | Expansiones, dataslates, Forge World, Imperial Armour | Dataslates & Supplements, Forge World |
| `Game Aids` | Plantillas, cartas, terreno imprimible | Tools and Extras, Cards |
| `Fan & Homebrew`, `Fiction`, `Background`, `Misc` | como en rol | |

### Serie o producto (opcional)

Solo cuando un mismo producto o serie tiene **5 o más ficheros**: `Adventures/Rise of the Runelords`,
`Organized Play/Adventure League/S9 - Descent into Avernus`, `Fiction/Gaunt's Ghosts`. Un fichero
suelto va directamente en su tipo.

### Carpetas de trabajo

Las que empiezan por `_` no son destinos: `_Por clasificar` (juego y edición claros, tipo no), `_Revisar`.
El clasificador no aprende de ellas.

## Ejemplos

| Hoy | Con el esquema |
|---|---|
| `RPG/D&D/Adventures/5e (Wizards of the Coast, 2014)/Adventure League/S9 - Descent into avernus` | `RPG/D&D/5e (Wizards of the Coast, 2014)/Organized Play/Adventure League/S9 - Descent into Avernus` |
| `RPG/D&D/Rulebooks/5e (Wizards of the Coast, 2014)/Core Books` | `RPG/D&D/5e (Wizards of the Coast, 2014)/Core Rules` |
| `RPG/D&D Third Party/5e (2014)/Kobold Press/Tome of Beasts.pdf` | `RPG/D&D 3rd Party/5e (2014)/Supplements/Tome of Beasts.pdf` (editorial: Kobold Press, en el catálogo) |
| `RPG/Paizo/2nd Edition/Adventure Paths/Kingmaker 2E` | `RPG/Pathfinder/2e (Paizo, 2019)/Adventures/Kingmaker` |
| `Wargames/Miniaturas/Games Workshop/Warhammer 40K/9e (2020)/01) Rulebooks & Missions` | `Wargames/Games Workshop/Warhammer 40K/9e (Games Workshop, 2020)/Rules` |
| `…/Warhammer 40K/8e (2017)/1) Codex & Index/2) Codexes` | `Wargames/Games Workshop/Warhammer 40K/8e (Games Workshop, 2017)/Army Books` |
| `…/Warhammer 40K/Black Library/Novels/Gaunt's Ghosts` | `Wargames/Games Workshop/Warhammer 40K/Any Edition/Fiction/Gaunt's Ghosts` |
| `…/Warhammer 40K/Warhammer 40k - 9 Edicion - Reglas Basicas.pdf` | `Wargames/Games Workshop/Warhammer 40K/9e (Games Workshop, 2020)/Rules/Spanish/` |

## Cómo clasificaría el sistema con este esquema

1. **Juego**: por el nombre, los vecinos, el código de serie y, si hace falta, Claude.
2. **Edición**: por el nombre (`5e`, `2nd Edition`), el año del PDF y la editorial.
3. **Type**: por palabras del nombre (`Core`, `Codex`, `FAQ`, `DDAL`…) y, sobre todo, Claude mirando la portada.
4. La ruta sale sola: `RPG/<Game>/<Edition>/<Type>` (en wargames, con `<Publisher>` delante). Un juego nuevo tiene sitio por regla, sin inventar carpetas.
5. Si falta una de las tres respuestas: `…/<Juego>/<Edición>/_Por clasificar` o la Entrada, como ahora.

## Cómo se migraría (si se decide)

- **Juego a juego**, no todo de golpe. Primero uno de prueba (Warhammer 40K o D&D 5e).
- Para cada juego: una **tabla carpeta actual → carpeta nueva** propuesta automáticamente y revisada por ti.
- El mismo método de siempre: **ensayo** primero, CSV de cada movimiento, nunca se sobrescribe, y
  además un script para **deshacer** el juego entero si no convence.
- Después de cada juego: medir cuánto mejora el clasificador allí.
