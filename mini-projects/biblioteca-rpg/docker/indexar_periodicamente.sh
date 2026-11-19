#!/bin/sh
# Indexa al arrancar y luego cada INTERVALO_HORAS, con la prioridad más baja de CPU
# para no molestar al resto de servicios de OMV3.
# Cada paso es un proceso aparte: así la memoria de uno se libera antes del siguiente.
#   1. recorrer la biblioteca y actualizar el catálogo
#   2. portadas pendientes
#   3. año, editorial e ISBN del texto de los PDF
#   4. RPGGeek/BGG (solo si hay BGG_TOKEN), como mucho GEEK_MINUTOS por pasada
INTERVALO_HORAS="${INTERVALO_HORAS:-6}"
while true; do
    echo "=== $(date '+%Y-%m-%d %H:%M') indexando ==="
    nice -n 19 python -m biblioteca_rpg.indexador --sin-portadas ${INDEXADOR_ARGS:-}
    nice -n 19 python -m biblioteca_rpg.indexador --solo-portadas ${INDEXADOR_ARGS:-}
    nice -n 19 python -m biblioteca_rpg.enriquecer pdf
    if [ -n "${BGG_TOKEN:-}" ]; then
        nice -n 19 python -m biblioteca_rpg.enriquecer geek --minutos "${GEEK_MINUTOS:-240}"
    fi
    echo "=== $(date '+%Y-%m-%d %H:%M') siguiente pasada en ${INTERVALO_HORAS} h ==="
    sleep "$((INTERVALO_HORAS * 3600))"
done
