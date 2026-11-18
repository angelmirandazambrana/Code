#!/bin/sh
# Indexa al arrancar y luego cada INTERVALO_HORAS, con la prioridad más baja de CPU
# para no molestar al resto de servicios de OMV3.
# El recorrido y las portadas van en procesos separados: así la memoria del recorrido
# (≈90.000 rutas) se libera antes de las horas de generar portadas.
INTERVALO_HORAS="${INTERVALO_HORAS:-6}"
while true; do
    echo "=== $(date '+%Y-%m-%d %H:%M') indexando ==="
    nice -n 19 python -m biblioteca_rpg.indexador --sin-portadas ${INDEXADOR_ARGS:-}
    nice -n 19 python -m biblioteca_rpg.indexador --solo-portadas ${INDEXADOR_ARGS:-}
    echo "=== $(date '+%Y-%m-%d %H:%M') siguiente pasada en ${INTERVALO_HORAS} h ==="
    sleep "$((INTERVALO_HORAS * 3600))"
done
