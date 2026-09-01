<p align="center">
  <img src="../assets/Recording%202026-08-29%20103658.gif" width="600">
</p>

<p align="center">
  <a href="../README.md">English</a> ·
  <a href="README.zh.md">中文</a> ·
  <a href="README.ja.md">日本語</a> ·
  <a href="README.fr.md">Français</a> ·
  <b>Español</b> ·
  <a href="README.ru.md">Русский</a>
</p>

## "Too Many Tools" — un agente de programación para la línea de comandos. Edita archivos en un espacio de trabajo aislado, ejecuta código en una docena de lenguajes, y hace commit y push automáticamente en cualquier repositorio.

>**Requiere Python 3.8+.**

## El comando es `tmtcode`

Instalarlo deja un único comando en tu PATH:

```bash
tmtcode
```

Ejecútalo desde cualquier directorio del sistema. **El directorio en el que lo ejecutas
pasa a ser el proyecto sobre el que trabaja TMT.**

```bash
cd ~/Projects/MyWebsite && tmtcode      # TMT trabaja sobre ~/Projects/MyWebsite
cd ~/Documents/MyProject && tmtcode     # TMT trabaja sobre ~/Documents/MyProject
```

Instala TMT una sola vez, en cualquier sitio. Nunca lo copias dentro de un proyecto, y
ningún proyecto necesita tener archivos de TMT dentro.

## Instalación

```bash
git clone https://github.com/lllons/TMT.git
cd TMT
pip install -e .                 # deja `tmtcode` en el PATH
pip install -e ".[live]"         # opcional: añade requests y rich para streaming y color
```

El agente en sí no necesita nada más allá de la biblioteca estándar; `requests` y `rich`
solo añaden streaming en vivo y color, y TMT funciona igualmente sin ellos.

Después de instalarlo, deja el clon donde está y ejecuta `tmtcode` desde donde tengas tu
trabajo. El clon es la casa de TMT, no tu proyecto.

Sin instalar nada, un clon también se ejecuta directamente, y desde cualquier sitio:

```bash
python /path/to/TMT/TMT.py                    # el directorio actual es el proyecto
python /path/to/TMT/TMT.py ~/Projects/MyWebsite
```

Windows: `py`. macOS/Linux: `python3`.

## Los dos directorios

TMT mantiene estrictamente separados su propia instalación y tu proyecto. Son dos cosas
distintas y están pensadas para seguir siéndolo.

| | Qué vive ahí | Dónde está |
|---|---|---|
| **Directorio de instalación** | el código fuente de TMT, tu clave de API guardada, la identidad de coautor de git de TMT, sus registros | donde lo hayas clonado — `~/tools/TMT`, `C:\Coding\TMT` — se fija una vez y no se mueve nunca |
| **Directorio del proyecto** (el espacio de trabajo) | los archivos que TMT lee, edita, ejecuta y confirma | donde hayas ejecutado `tmtcode` |

Solo se modifica el directorio del proyecto. Los archivos propios de TMT permanecen en el
directorio de instalación estés en el proyecto que estés, así que es el mismo agente —
misma clave, misma dirección de coautor — en todas partes.

Dicho una vez más con claridad: **no copias TMT dentro de un proyecto para usarlo en ese
proyecto.** Un clon, una instalación, y luego `cd` a cualquier proyecto y escribe `tmtcode`.

## Elegir el directorio del proyecto

| Comando | Directorio del proyecto |
|---|---|
| `tmtcode` | el directorio actual |
| `tmtcode ../other-repo` | se resuelve respecto al directorio actual y después se convierte en absoluto |
| `tmtcode /abs/path/to/project` | esa ruta |
| `tmtcode --dir PATH` | lo mismo que el `PATH` posicional, se mantiene por compatibilidad |

Una ruta relativa se resuelve respecto al directorio en el que ejecutaste el comando y
después se convierte en absoluta. Dar a la vez un `PATH` posicional y un `--dir` que
nombren directorios distintos es un error, y TMT termina sin arrancar.

TMT usa exactamente el directorio que se le indicó. No sube por el árbol buscando la raíz
de un proyecto: ejecútalo en `MyWebsite/src` y el espacio de trabajo es `MyWebsite/src`.

La ruta resuelta se imprime al arrancar, de modo que una ejecución desde el sitio
equivocado resulta evidente:

```
Workspace: C:\Projects\my-repo
```

Todo lo que quede fuera de ese directorio está vedado: una ruta que se salga del espacio
de trabajo se rechaza, no se sigue.

## Permisos y límites

- TMT puede crear, sobrescribir y borrar archivos en cualquier punto del directorio del
  proyecto, y nada de lo que hace ahí es recuperable a menos que el directorio sea un
  repositorio git. Necesita permisos normales de lectura y escritura sobre él.
- El directorio de instalación debe ser escribible: ahí se escriben `.tmt_key` y `logs/`.
  `.tmt_git` y `.tmt_git.local` solo se leen desde ahí.
- Un directorio se selecciona, nunca se crea. TMT rechaza una ruta que no existe, un
  archivo, la raíz de un sistema de archivos y tu directorio personal.
- Si el directorio ya tiene archivos y no está dentro de un árbol de trabajo de git, TMT
  describe aquello a lo que está a punto de apuntar y pregunta antes de empezar. Un
  repositorio git es su propio deshacer, así que en ese caso arranca sin preguntar.
- TMT nunca ejecuta comandos de shell. Solo ejecuta código a través de `run_file`, y
  únicamente lanza las dos aplicaciones que se enumeran más abajo.
- El push usa las credenciales de git que ya tengas. TMT no almacena ninguna ni implementa
  ningún inicio de sesión.

## La pantalla de arranque

Cada ejecución de `tmtcode` abre con la misma pantalla: el logotipo de TMT llenando la
terminal y, debajo,

```
                              Press Enter to Continue
```

La línea late mientras espera: el degradado recorriéndola en una terminal con color, un
pulso lento de grosor en una sin color, y nada en absoluto donde no se pueden usar
secuencias de escape, porque aquí tampoco el color es nunca el mensaje. Enter es la única
tecla que continúa; Ctrl-C cierra TMT. Todo lo demás se ignora, de modo que una primera
tarea escrita antes de que la pantalla se asiente no puede desencadenar nada.

**La pantalla de arranque aparece siempre.** No es un ajuste y no hay nada que desactivar.
Lo que sí es un ajuste es lo que ocurre después de pulsar Enter.

### Después de Enter: la comprobación de actualizaciones

Con `Auto Update on Launch` **activado** (el valor por defecto), TMT mira su propio clon de
git para ver si existe una versión más reciente, y lo dice en esa misma pantalla:

```
                              Searching for updates...
```

y después una de estas:

```
                                    Up to date.            nothing was pulled, nothing restarted
                          Update complete. Restarting...   a fast-forward was applied
                            Continuing without updating.   an update could not be taken safely
                Update check failed. Continuing without update.
```

Con el ajuste **desactivado**, nada de eso ocurre y TMT no finge lo contrario: no se muestra
ninguna línea de «búsqueda» por una búsqueda que nunca se hizo. Continúa directamente.

Tras eso, TMT sigue exactamente igual que siempre: la configuración de la clave de API si
esta instalación todavía no se ha configurado y, en caso contrario, la pantalla de inicio
habitual.

### Cuándo se actualiza TMT y cuándo no

Solo se actualiza cuando la actualización es inequívocamente segura, y nunca toca tu trabajo.

| Qué encuentra | Qué hace |
|---|---|
| ya está al día | nada. Ni pull ni reinicio |
| el remoto va por delante, árbol limpio, fast-forward posible | hace fast-forward y después reinicia |
| **cambios locales sin confirmar** | se niega, y lo dice. Tus cambios quedan intactos |
| **la rama ha divergido** — local y remoto se han movido los dos | se niega. Los commits locales nunca se descartan |
| sin upstream configurado, o un HEAD desacoplado | dice que no puede saberlo, y continúa |
| no es un clon de git en absoluto | continúa |
| sin red, sin git, un remoto incorrecto, una fusión fallida | informa del fallo y continúa |

Trabaja sobre la rama que ya tienes activa y nunca crea, cambia ni fuerza ninguna. Usa
`git fetch` y `git merge --ff-only` y nada más: **nunca ejecuta `git reset --hard`,
`git clean`, un checkout forzado ni un `git pull` a secas** — un pull puede fusionar, y una
fusión durante el arranque es exactamente lo que no debe ocurrir. Una prueba lee el propio
código fuente del actualizador y comprueba que esos comandos no aparecen en él.

TMT sigue siendo utilizable sin internet. Una comprobación de actualización fallida es una
línea en la pantalla de arranque y nada más.

### Reinicio

Una actualización correcta reemplaza el proceso por uno nuevo, de modo que el código nuevo
se ejecute de verdad en lugar de quedarse los módulos antiguos importados. Tu línea de
comandos se conserva: `tmtcode --dir ~/project` vuelve como `tmtcode --dir ~/project`.

Después vuelves a ver la pantalla de arranque, lo cual es lo esperado: la pantalla de
arranque forma parte de cada inicio. El proceso reiniciado se encuentra al día y continúa.
**No puede entrar en bucle**: como mucho se produce un reinicio automático por arranque, y
el segundo proceso sabe que es el segundo.

### Cómo desactivarlo

Settings → `Auto Update on Launch`, Enter para alternar:

```
  AI Provider            Which service answers a request
  API Key                The credential that service is given
  Model                  Which model TMT runs on
> Auto Update on Launch  Check for a newer TMT after the launch screen  ON
  Back                   Return to the menu
```

Se guarda en `.tmt_autoupdate` en el directorio de instalación, junto a los ajustes de
modelo y esfuerzo, así que pertenece a la instalación y no a un proyecto y sobrevive a los
reinicios. Un archivo que falta significa activado; un archivo que nadie puede leer, o uno
editado hasta quedar sin sentido, también significa activado en lugar de un error al
arrancar.

**Desactivarlo no desactiva la pantalla de arranque.** La pantalla se muestra igualmente.

## Primer arranque

El primer arranque pide una [clave de OpenRouter](https://openrouter.ai/keys) y la guarda en
`.tmt_key` en el directorio de instalación (ignorado por git). Define `OPENROUTER_API_KEY`
para saltarte ese paso. Se pide una vez por instalación, no una vez por proyecto.

Escribe una tarea en el indicador `Task>`. `quit` o `exit` para salir. Ctrl-C cancela la
tarea actual sin cerrar TMT.

Los archivos de menos de 8 KB se muestran al modelo automáticamente, hasta un número y un
tamaño total fijos; el listado lo indica cuando se detiene antes de tiempo. Los archivos
más grandes se leen bajo demanda.

## Qué puedes pedir

Lenguaje natural. TMT elige las acciones por su cuenta.

```
Task> write a python script that fetches a URL and prints the status code
Task> what does report.py do?
Task> find every TODO in src and list them
Task> change the timeout in net.py from 5 to 30 seconds
Task> run hello.py
Task> open notes.txt in notepad
Task> commit the changes and push to main
```

## Hablar contigo: `send_message` y `end_conversation`

TMT tiene exactamente dos formas de poner palabras en tu pantalla, y toda la
diferencia entre ellas está en si la tarea continúa después.

| Acción | Te muestra texto | Termina la tarea |
|---|---|---|
| `send_message` | sí | **no** — el control vuelve al agente, siempre |
| `end_conversation` | sí | **sí** — y es la única acción que termina una |

**`send_message` sirve para decir cosas por el camino.** «Primero voy a leer el
analizador», «las pruebas están en verde, así que ahora toca la documentación»,
«este archivo es más grande de lo que esperaba». Se imprime en la sesión, donde
puedes volver atrás para leerlo, y después TMT continúa exactamente desde donde
estaba. Puede usarse tantas veces en una tarea como merezca la pena; no hay
límite y no tiene nada de definitivo.

**`end_conversation` es el final.** Su mensaje es el resumen con el que te
quedas mirando, y por eso al agente se le dice que el trabajo que no se describa
ahí es como si no hubiera ocurrido. No hay una segunda forma de parar: ni un
`done` aparte, ni un indicador en un mensaje que lo convierta en silencio en el
último. TMT termina una tarea con esta acción o no la termina.

**Querer terminarla no es lo mismo que tener permiso.** `end_conversation` es lo
que retienen las barreras de finalización, y cualquier capacidad que hayas
activado para esa petición puede rechazarla: un plan con pasos pendientes, una
revisión que no ha pasado, una verificación que no se ha ejecutado o que ha
encontrado una comprobación fallida. Un rechazo no es un error y no termina el
turno: se le entrega el motivo a TMT, este vuelve al trabajo y la respuesta sigue
sin decirse. Consulta
[Capacidades](#capacidades-plan-review-verify).

Los agentes en segundo plano no tienen ninguno de los dos canales en un sentido
útil: nadie los está leyendo, así que un mensaje cuesta un paso y no llega a
nadie, y el final es un informe al agente principal en su lugar. Consulta
[Agentes en segundo plano](#agentes-en-segundo-plano).

## Capacidades: `/plan`, `/review`, `/verify`

Tres de las cosas que TMT puede hacer no son trabajo de herramienta corriente.
Escribir un plan y quedar sujeto a él, hacer que un segundo agente audite el
diff, y ejecutar las comprobaciones propias del repositorio cuestan cada una una
ejecución entera adicional del modelo, ponen una columna en la pantalla y pueden
rechazar la respuesta final del propio TMT. Son tuyas para gastarlas, así que
están desactivadas salvo que las pidas, y las pides escribiendo el comando en tu
petición.

| Comando | Activa |
|---|---|
| `/plan` | el flujo de planificación — TMT escribe los pasos y no puede terminar hasta que estén hechos |
| `/review` | revisión de código independiente — un agente aparte, de solo lectura, audita el trabajo |
| `/verify` | Verificación Inteligente — las comprobaciones que este repositorio realmente tiene, ejecutadas de verdad |

```
Task> build me an authentication system
        nothing gated. Ordinary tools, ordinary answer.

Task> build me an authentication system /plan
        plans the work and is held to the plan. No review, no verification.

Task> fix this implementation /review
        an independent reviewer audits it before the answer goes out.

Task> add the endpoint /plan /verify /review
        the whole pipeline: plan, implement, verify, review, fix, answer.
```

**La barra es toda la distinción, y `verify` por sí solo no basta.**
«verify this code», «please verify this», «verified» y «verification» son cosas
que la gente dice mientras pide trabajo corriente, y ninguna de ellas enciende el
motor. Solo lo hace `/verify`. Lo mismo vale para `plan` y `review`:
«review my code please» es una petición de opinión, `/review` es una petición del
revisor independiente, con barrera y con límite de ciclos.

Tampoco lo hace una palabra más larga que empiece por una de ellas. `/planning`,
`/planner`, `/plan123`, `/reviewing` y `/verification` son texto corriente, y lo
mismo un comando dentro de una ruta: `src/review` y `abc/verify` son rutas, no
comandos.

El resto de las reglas son las que cabría imaginar:

- **En cualquier parte de la petición.** Al principio, en medio, al final o en
  líneas propias dentro de un bloque pegado. `/plan Build it`, `Build it /plan` y
  `Build the /plan feature` son la misma petición.
- **Cualquier número de veces.** `/plan ... /plan` activa la planificación una
  vez. No existe tal cosa como dos planes.
- **Cualquier combinación de mayúsculas.** `/PLAN` funciona, y sigue siendo
  `/PLAN` en tu pantalla: TMT da estilo a tu texto y nunca lo reescribe.
- **Independientes.** `/plan` no activa la revisión ni la verificación, y ninguna
  de esas dos activa las otras. Tú eliges el flujo de trabajo.
- **Una petición cada vez.** Una capacidad queda autorizada para la petición que
  la pidió. La siguiente pregunta parte de cero a menos que también la pida.

**Solo tú puedes activarlas.** Ni TMT, ni un agente en segundo plano, ni un
revisor, ni un archivo que haya leído. Un modelo que decide que la tarea parece
lo bastante grande para un plan, escribe `/plan` en su propio razonamiento y llama
a la acción es rechazado por el motor de ejecución: la autorización se lee de la
línea que tú escribiste y de nada más. Eso se aplica dos veces: los verbos no
autorizados se dejan fuera del prompt por completo, y el despachador los rechaza
de nuevo si aun así se intenta usar alguno.

**Se resaltan mientras escribes.** Un comando válido en el cuadro de entrada lleva
el degradado rojo → naranja → verde a lo largo, de modo que puedas ver qué has
activado antes de pulsar Enter, y verlo desaparecer si lo escribes mal.
Solo se pinta el comando exacto: `verify` se queda liso y `/verification` se queda
liso. En una terminal sin color el comando se destaca en negrita y subrayado en su
lugar, y en una ejecución canalizada no hay estilo alguno: la fila sigue leyéndose
`/plan`, que es el comando escrito tal cual.

Mientras corre el turno, lo que hayas autorizado se enumera en lo alto de la
columna derecha:

```
                                                        CAPABILITIES 2
                                                        ● /plan
                                                        ● /verify

                                                        PLAN 2/5
```

**`/plan`, `/review` y `/verify` por sí solos siguen siendo los informes** que
siempre han sido; consulta [Comandos de barra](#comandos-de-barra). Una línea que
no es más que el comando te muestra qué está haciendo TMT; una línea con una tarea
dentro autoriza la capacidad para esa tarea.

### Archivos

| Acción | Propósito |
|---|---|
| `write_file` / `write_files` | Crear un archivo, o varios de una vez |
| `patch_file` | Buscar y reemplazar — la opción por defecto para las ediciones |
| `replace_lines` | Reemplazar un rango exacto de líneas |
| `append_file` | Añadir al final de un archivo |
| `read_file` / `read_lines` | Leer un archivo entero, o un rango de líneas |
| `glob` | Encontrar archivos y directorios por un patrón de ruta |
| `grep` | Buscar en el contenido de los archivos e informar de la ruta, el número de línea y la línea |
| `copy_file` / `rename_file` / `delete_file` | Mover, renombrar, eliminar |
| `create_folder` / `delete_folder` | Carpetas (el borrado recursivo hay que pedirlo explícitamente) |
| `list_files` | Listar el espacio de trabajo |

Las rutas se interpretan respecto al directorio del proyecto, y todo lo que se
resuelva fuera de él se rechaza. Solo ese directorio se lista, se lee o se escribe.

Editar un archivo existente usa `patch_file`, no una reescritura, así que las líneas
que no se tocan siguen sin tocarse. A los archivos de Python se les comprueba la
sintaxis antes de escribirlos; una edición que los rompe se rechaza en lugar de
guardarse.

### El plan

**Pídelo con `/plan` en tu petición.** Sin ese comando TMT no escribe un plan y
nada de esto condiciona la respuesta. Consulta
[Capacidades](#capacidades-plan-review-verify).

Con `/plan`, para cualquier cosa de entidad — añadir una función, corregir un fallo por todo
el repositorio, refactorizar un subsistema, actualizar la documentación de un proyecto
entero — TMT escribe un plan antes de empezar y lo recorre delante de ti. Aparece como una
columna a la derecha del área en vivo mientras trabaja, y ahí se queda, terminado, junto al
siguiente indicador.

```
                                                        PLAN 2/5
                                                        ─────────────────────────
                                                        S1 ✓ Inspect repository
 09:14 · OpenRouter · MiniMax M3                        S2 ✓ Find and…erminology
 ───────────────────────────────────────────────────    S3 ● Update documentation
 > Describe your next task                              S4 ○ Run tests and verify
 ───────────────────────────────────────────────────    S5 ○ Explain changes
```

| Marca | Estado | Color | Significa |
|---|---|---|---|
| `✓` | completado | verde | el trabajo de ese paso está realmente hecho |
| `●` | en curso | naranja | el único paso en el que se está trabajando ahora |
| `○` | pendiente | rojo | todavía por venir |
| `!` | bloqueado | ámbar | no puede avanzar, y sigue contando como no terminado |

Hay exactamente un paso en curso cada vez. Completar uno promueve el siguiente por su
cuenta. El color es confirmación, nunca el mensaje: todos los estados llevan también una
marca, y la columna entera degrada a `+ > - !` y a reglas ASCII en una terminal que no
puede dibujar el resto.

**El plan es un contrato, no una barra de progreso.** TMT no tiene permitido terminar una
tarea mientras haya un paso pendiente. Una respuesta final enviada con trabajo por hacer no
se te muestra en absoluto: el motor de ejecución la rechaza, le devuelve al modelo la lista
de pasos que todavía debe, y el turno continúa. Eso lo impone el programa en lugar de
pedirse en el prompt, así que un modelo que decide que ha terminado no hace que haya
terminado:

```
Task> add the feature
 · Planning the work in two steps.
 ◆ Plan created with 2 steps.
 ▲ Plan not finished - 2 steps outstanding, next is S1 Implement the feature. Continuing.
 · The feature is in; running the tests next.
 ◆ S1 (Implement the feature) in_progress -> completed.
 · Suite is green.
 ◆ S2 (Run the tests) in_progress -> completed.
 ┌──────────────────────────────────────────────────────────────┐
 │ Added the feature and the suite is green: 12 tests, 0 failures.│
 └──────────────────────────────────────────────────────────────┘
```

El plan puede revisarse siempre que el trabajo resulte ser distinto de lo esperado: pasos
renombrados, añadidos, eliminados, o el plan entero reemplazado. Dos cosas no pueden pasar.
Un paso completado nunca se reabre: un paso terminado sigue terminado, y un plan cuya forma
era equivocada se reemplaza de golpe en lugar de deshacerse. Y un plan sobre el que ya se ha
trabajado no se puede abandonar: esa era la única vía para rodear la barrera, así que
borrarlo se rechaza en cuanto hay un paso completado. Terminarlo y rehacerlo son cosas
visibles en pantalla; abandonarlo en silencio no lo sería.

**No todo recibe un plan.** «¿Qué hace esta función?» es una sola respuesta, y un plan para
eso sería ruido en pantalla y una barrera sobre la propia respuesta de TMT. Los planes son
para trabajo con etapas.

**El plan pertenece a la tarea, no a la sesión.** Se retira en el momento en que haces la
siguiente pregunta, de modo que un plan sin terminar nunca puede retener la respuesta a algo
no relacionado. No se escribe nada en disco. Los agentes en segundo plano no pueden verlo ni
cambiarlo: es el contrato del agente principal contigo, y que un trabajador completara un
paso permitiría a TMT terminar sobre trabajo que solo se había atribuido.

**En una terminal de menos de 45 columnas** la columna no se dibuja — el cuadro de entrada
necesita más el espacio — y `/plan` imprime lo mismo que texto corriente a cualquier ancho.

### Entender un repositorio

Nueve acciones para orientarse en un código base sin leerlo entero. Cada una responde a una
pregunta, y a TMT se le dice que elija la más estrecha que encaje.

| Acción | Propósito | Recurre a ella cuando |
|---|---|---|
| `tree` | Directorios, archivos, tamaños, anidamiento. No lee contenidos | Necesitas la forma del proyecto |
| `glob` | Archivos y directorios que encajan con un patrón de ruta. `*` se detiene en una `/`, `**/` significa cualquier profundidad, y un patrón sin `/` encaja con un nombre esté donde esté | Necesitas saber qué archivos existen, o dónde está uno |
| `grep` | Buscar dentro de los archivos, informando de la ruta, el número de línea y la línea. Exacta y sensible a mayúsculas por defecto; la consulta puede abarcar varias líneas | Sabes el texto que buscas |
| `find_symbol` | Dónde se *define* una función, clase, método, constante o tipo | Quieres una definición, no una mención |
| `code_map` | Qué define esto, qué lo importa, qué importa él, dónde se referencia | Necesitas saber a qué afectaría un cambio |
| `replace_across` | La misma edición exacta en muchos archivos | Estás renombrando algo que usa todo el proyecto |
| `related_tests` | Lee el diff de git y nombra las pruebas que merece la pena ejecutar | Cambiaste una cosa y no quieres ejecutarlo todo |
| `remember` / `recall` | Notas duraderas sobre este proyecto, guardadas entre sesiones | Algo te costó tiempo averiguarlo |

```
Task> show me the project structure
Task> find every place that calls self.workspace_root
Task> where is calculate_total defined?
Task> what imports agent_file_ops?
Task> rename old_function_name to new_function_name across src
Task> which tests should I run for what I just changed?
```

**`glob` encuentra archivos por ruta o por nombre; `grep` encuentra texto dentro de los
archivos.** Esa es toda la distinción, y es la que merece la pena tener clara: el orden
que funciona es `glob` para encontrar los archivos candidatos, `grep` para encontrar las
líneas dentro de ellos, `read_lines` para leer esa región, luego editar y luego probar —
en vez de leerse un repositorio entero para dar con una sola línea.

```json
{"action": "glob", "pattern": "agent_*.py"}
{"action": "glob", "pattern": "testing/**/*.py"}
{"action": "grep", "query": "end_conversation"}
{"action": "grep", "query": "def run_file", "glob": "agent_*.py"}
{"action": "grep", "query": "timeout", "path": "src", "ignore_case": true}
```

`grep` es exacta y sensible a mayúsculas por defecto, como la herramienta de la que toma
el nombre. `"ignore_case": true` la vuelve laxa, `"regex": true` lee la consulta como una
expresión regular, `"context"` añade líneas a cada lado de cada coincidencia, y `"path"` o
`"glob"` restringen qué archivos se leen siquiera. Nunca devuelve un archivo entero:
obtienes la ruta, el número de línea y la línea, y `read_lines` te trae el resto.

**`replace_across` hace una vista previa por defecto.** Informa de cuántos archivos y
cuántas apariciones *cambiaría* y no escribe nada. Enviar la misma acción de nuevo con
`"apply": true` la ejecuta. Se conservan los finales de línea y la codificación, se omiten
los archivos binarios, y un reemplazo que dejaría un archivo de Python imposible de analizar
se rechaza en lugar de escribirse.

**Los hechos y las conjeturas se etiquetan de forma distinta.** Los símbolos de Python se
encuentran analizando el archivo, así que esas respuestas son exactas; los otros lenguajes se
buscan léxicamente y así lo dicen. `related_tests` separa lo que el diff demuestra de lo que
solo está suponiendo. Nada presenta una heurística como una medición.

**La memoria del proyecto** se guarda junto a los propios ajustes de TMT, indexada por
proyecto, nunca dentro de tu repositorio: la misma regla que para cualquier otro estado de
TMT. Las notas se revisan antes de escribirse y todo lo que tenga forma de clave, token o
contraseña se rechaza.

### Ejecutar código

`run_file` ejecuta y devuelve la salida. Python, JavaScript, TypeScript, Ruby, PHP, Lua,
Perl, R, Go, C, C++, Java. Tiempo límite de 10 segundos. La cadena de herramientas tiene que
estar en tu PATH. El código se ejecuta con el directorio del proyecto como directorio de
trabajo.

### Aplicaciones

`open_app` lanza el Bloc de notas, o el Explorador con un archivo seleccionado. Nada más:
TMT nunca ejecuta comandos de shell.

## Git

TMT hace commit en el repositorio que contiene el directorio del proyecto, no en el
repositorio propio de TMT. Tú sigues siendo el autor y quien confirma cada commit que hace;
a TMT se le acredita a tu lado con una línea final `Co-authored-by`.

```
Task> commit this                        commits, does not push
Task> commit these changes and push       commits and pushes
Task> push this to main                   targets main
Task> fix the bug                         edits only, no commit, no push
```

Commit y push están separados. TMT solo hace push cuando tus propias palabras lo pidieron:
«fix the bug» nunca desencadena un push, y terminar una edición tampoco.

Acciones: `git_status`, `git_diff`, `git_identity`, `git_commit`, `git_push`.

Prepara solo los archivos que ha cambiado, así que tu trabajo no relacionado sigue sin
confirmar. Nunca crea una rama, nunca se inventa un remoto y nunca fuerza un push. Si un
push falla, el commit se queda en local y recibes el error real.

### Coautoría de TMT

TMT no hace commit como tú, y no hace commit en tu lugar. La identidad de git del propio
repositorio es el autor y quien confirma. TMT añade una línea final al mensaje y nada más.
Un commit, los dos acreditados en él.

Un commit hecho por TMT, tal como lo informa git:

```
$ git log -1 --format=fuller
commit f1977e70a471011bf9b5ab643aecdf5e18a8e8fa
Author:     Liam <liam@example.com>
AuthorDate: Sat Aug 29 13:10:01 2026 +1200
Commit:     Liam <liam@example.com>
CommitDate: Sat Aug 29 13:10:01 2026 +1200

    Add a greeting file

    Co-authored-by: TMT code <TMT.tmt.code@gmail.com>
```

Es una línea final de git, no una línea de prosa que casualmente lleva dos puntos, así que
el propio git te la devuelve:

```
$ git log -1 --format=%(trailers:key=Co-authored-by)
Co-authored-by: TMT code <TMT.tmt.code@gmail.com>
```

Qué significa eso en la práctica:

- El autor y quien confirma son quienes diga la configuración de git del repositorio. TMT
  nunca se pone a sí mismo en ninguno de los dos campos, y nunca escribe en tu configuración
  de git, ni global ni por repositorio.
- Si no tienes una identidad de git configurada, git rechaza el commit. TMT lo informa, te
  dice que definas `user.name` y `user.email` tú mismo, y no se pone como autor sustituto.
  No se confirma nada.
- TMT añade la línea final solo a los commits que él crea. Un `git commit` que ejecutas tú
  queda intacto.
- Un mensaje que ya acredita la dirección de TMT recibe una línea final, no dos. La
  coincidencia es por dirección, así que la misma dirección con otro nombre visible sigue
  contando como ya acreditada.
- Las líneas finales existentes sobreviven. Un `Co-authored-by:` de otra persona se conserva
  y TMT se añade junto a él; un bloque `Signed-off-by:` se une a él en lugar de empujarlo a
  un párrafo nuevo.
- El historial nunca se reescribe. Si el commit terminado carece por lo que sea de la línea
  final, TMT lo informa y deja el commit en paz en lugar de enmendarlo.
- Sin una dirección de TMT configurada, o con el marcador de posición de fábrica todavía
  puesto, TMT se niega a hacer commit y no prepara nada.

El crédito en GitHub es una cuestión aparte, y TMT no controla la respuesta:

- TMT decide los metadatos del commit: la línea final, y solo la línea final.
- GitHub decide si acredita al coautor. Compara la dirección de la línea final con las
  direcciones verificadas en una cuenta. Una dirección verificada en ninguna cuenta no se
  acredita a nadie, y el nombre visible por sí solo no hace nada.
- Incluso cuando la dirección sí coincide, los datos de contribuidores y de perfil de GitHub
  pueden ir con retraso. Un push no cambia necesariamente el gráfico de contribuciones al
  instante.
- Recibir crédito no es lo mismo que tener permiso para hacer push. La autenticación es algo
  aparte y sigue siendo tuya.

### Identidad de coautor

```
TMT_GIT_NAME=TMT code
TMT_GIT_EMAIL=someone@example.com
```

La dirección bajo la que se acredita a TMT. Nunca se escribe en un commit como autor. Se lee
en este orden: variables de entorno `TMT_GIT_*`, después `.tmt_git.local` (ignorado por git,
por máquina) y después `.tmt_git` (versionado, se distribuye con el proyecto). El nombre
usa `TMT code` por defecto. El correo no tiene valor por defecto, y nunca se toma de tu
configuración de git: tu configuración de git aporta el autor, no el coautor.

Ambos archivos están en el directorio de instalación, así que a TMT se le acredita bajo la
misma dirección en todos los proyectos a los que lo apuntes.

`.tmt_git` está versionado a propósito: un correo de commit es metadato público, no una
credencial, así que cada clon obtiene el mismo coautor TMT sin configuración alguna.
Contiene un nombre y una dirección y nada más. No pongas tokens, contraseñas ni claves en él,
ni en `.tmt_git.local`.

Ejecuta `git_identity` para ver qué fuente ganó, qué archivos se consultaron y si la
dirección es utilizable.

### Configurar la atribución en GitHub

El `.tmt_git` versionado nombra la dirección verificada en la cuenta de GitHub que representa
a TMT, de modo que un clon nuevo lo acredita correctamente sin configuración. Si ese correo
llega a ser un marcador de posición, TMT se niega a hacer commit en lugar de acreditar a un
coautor que no identifica a nadie. Para acreditar una cuenta tuya en su lugar:

1. Crea una cuenta de GitHub para TMT.
2. Añade y verifica una dirección en ella.
3. Pon esa dirección en `.tmt_git.local`, o en `.tmt_git` y confírmala una vez.

Cuatro cosas distintas, de las cuales TMT solo decide una:

- **Autoría** — el autor y quien confirma escritos en el commit. Tuyos. TMT los lee para
  informar de ellos y nunca los establece ni los cambia, y tu `user.name` y tu `user.email`
  no se modifican, ni global ni por repositorio.
- **Crédito de coautoría** — la línea final `Co-authored-by`. La única parte del commit que
  decide TMT.
- **Atribución en GitHub** — que GitHub haga coincidir la dirección de esa línea final con
  una cuenta verificada. Fuera del alcance de TMT, y un nombre visible por sí solo no hace
  nada.
- **Autenticación** — quién puede hacer push. Sigue siendo tuya: tu clave SSH, tu gestor de
  credenciales o tu sesión de `gh`. TMT no almacena credenciales y no implementa ningún
  inicio de sesión.

## Verificación

**Pídela con `/verify` en tu petición.** Sin ese comando no ocurre nada de esto y nada de
aquí condiciona la respuesta; la palabra `verify` a secas no basta.
Consulta [Capacidades](#capacidades-plan-review-verify).

Con `/verify`, antes de que TMT tenga permiso para decir que un trabajo está hecho ejecuta
las comprobaciones que este repositorio realmente tiene, y el motor de ejecución no le deja
responder hasta que pasen.

La clave es la distinción entre evidencia y opinión:

> «Esto debería funcionar» no es verificación. `43 passed, 0 failed` sí lo es.

TMT no le pregunta al modelo si el código funciona. Lee el repositorio, deduce con qué prueba,
analiza y construye este proyecto, lee el diff para ver qué cambió, elige las comprobaciones
que merece la pena ejecutar para *ese* cambio, las ejecuta e informa de los códigos de salida.
Nada de lo que escriba el modelo puede mover ese resultado: no hay ninguna clave en ninguna
acción que fije un estado, y una comprobación pasa cuando un proceso termina con cero y en
ningún otro momento.

### Qué decide ejecutar

**Prefiere tus comandos a sus propias conjeturas.** En orden:

1. un comando que este repositorio define por su nombre: un script de `package.json`, un
   objetivo de `Makefile`, un `run_tests.py` en la raíz
2. herramientas que este repositorio configura: `[tool.ruff]`, `[tool.mypy]`, `tsconfig.json`
3. el gestor de paquetes del proyecto: `npm`, `pnpm`, `yarn`, `bun`, `uv`, `poetry`
4. el comando estándar del ecosistema: `cargo test`, `go vet`, `pytest`
5. una conjetura, etiquetada como tal

Si tu `package.json` dice `"test": "vitest run"`, TMT ejecuta `npm run test`. No decide que
los proyectos de node usan jest. Si tu repositorio tiene un `run_tests.py`, ese es el comando
de pruebas, incluso donde `pytest` también funcionaría, porque ejecutar algo distinto de lo
que ejecutas tú y llamarlo tu verificación estaría mal aunque pasara.

La configuración de CI se lee como *evidencia* de qué herramientas usas de verdad, y nunca
como fuente de comandos. Nada de lo que ejecuta TMT es una cadena sacada de un archivo del
proyecto: lo que se toma es un nombre, y el comando se construye a partir de una tabla fija
alrededor de él. En ese camino no hay ningún shell en ninguna parte.

**Ejecuta las comprobaciones baratas antes que las caras**, y se detiene en la primera que
falla:

| Nivel | Qué |
|---|---|
| 1 | sintaxis y formato de lo que cambió |
| 2 | linting, comprobación de tipos, comprobaciones del compilador |
| 3 | las pruebas que nombran lo que cambiaste |
| 4 | las pruebas de alrededor |
| 5 | la compilación del proyecto |
| 6 | la suite completa |

Una vez que el comprobador de tipos ha fallado, los diez minutos que tardaría la suite de
integración son diez minutos gastados en medir un árbol que ya se sabe que está mal. Todo lo
posterior a un fallo se informa como omitido, con eso como motivo, de modo que lo que *no* se
comprobó queda visible en lugar de sobreentendido.

**Va más a fondo cuando el cambio es más arriesgado.** Autenticación, migraciones, esquema de
base de datos, contratos de API, concurrencia, límites del sistema de archivos, ejecución de
shell, configuración de dependencias o de compilación, o sencillamente muchos archivos a la
vez: todo eso recibe la suite completa. Un cambio que solo toca documentación recibe las
comprobaciones estáticas y ninguna ejecución de pruebas.

### Los cuatro resultados, mantenidos aparte

| | Significa |
|---|---|
| **PASSED** | el comando se ejecutó y salió con 0. La única clase de evidencia que hay |
| **FAILED** | el comando se ejecutó y salió con un valor distinto de cero. Algo va mal, y la salida dice qué |
| **SKIPPED** | no se ejecutó: la herramienta no está instalada, o una comprobación anterior ya había fallado |
| **ERROR** | no se pudo ejecutar o no terminó. No se sabe nada, y esto *no* es un fallo de tu código |

Nunca se reducen a un booleano. Un tiempo agotado no es un fallo; un linter que falta no es un
análisis superado. TMT no instalará nada para tapar uno de esos agujeros: una dependencia que
falta se informa, nunca se arregla en silencio.

### Qué aspecto tiene

```
VERIFY 1/3
✓ Syntax          passed
✓ Lint            passed
✗ Targeted tests  2 failed, 41 passed
– Full suite      not run: Targeted tests did not pass
```

`/verify` imprime la ejecución completa, incluida la salida de todo lo que haya fallado.

### Qué pruebas elige

Para un proyecto cuyo comando de pruebas admite rutas, TMT deduce a qué pruebas llega el
cambio y ejecuta esas primero. Un archivo de pruebas cuenta como **dirigido** cuando hay
evidencia de ello — nombra un módulo cambiado, importa un símbolo cambiado, o está donde la
propia convención de nombres del proyecto dice que debería — y como **relacionado** cuando es
una conjetura de alcance. Los dos se mantienen aparte y se nivelan aparte, porque una conjetura
presentada como una medición es peor que no seleccionar nada.

Donde el comando de pruebas del proyecto *no* puede acotarse a rutas — `npm test`, o un
`run_tests.py` que lo ejecuta todo — TMT lo dice y ejecuta la suite entera como evidencia de
pruebas. No ejecuta todo y lo etiqueta como dirigido.

### Cuándo ocurre

La verificación es obligatoria cuando el motor de ejecución ha visto **las dos** mitades de un
trabajo de entidad: un plan de tres o más pasos, y al menos un archivo escrito de verdad — los
mismos dos hechos con los que se decide una revisión, observados en lugar de afirmados. Una
pregunta, una lectura o un parche pequeño sin plan no quedan condicionados en absoluto.

Puedes anularlo en cualquiera de los dos sentidos con tus propias palabras. «…and run the
tests» lo activa; «no verification needed» lo desactiva. No decir nada lo deja en manos de la
evidencia.

También tiene su sitio en el plan. Un paso del plan cuyo nombre alude a la verificación no puede
marcarse como completado mientras la verificación esté pendiente: ese rechazo está en el código,
no en un prompt. Y la respuesta final necesita las tres cosas: **el plan completo, la
verificación superada y la revisión superada.** Ninguna exime de otra.

### El ciclo, y sus límites

Una comprobación fallida es información, no el fin de la tarea: TMT lee la salida, arregla lo
que indica y verifica de nuevo. Como mucho tres rondas; después de eso la respuesta se libera
en lugar de retenerse para siempre, llevando una línea que dice que la verificación nunca pasó.
El silencio sería el peor de los fallos.

**Un resultado superado caduca en el momento en que el código se mueve debajo.** Editar algo
después de que la verificación pasara significa que lo que pasó no es lo que se entregaría, y la
siguiente respuesta se retiene hasta que se haya verificado de nuevo. Eso es lo que hace que el
bucle de arreglar y verificar se cierre en lugar de ser una sugerencia.

Si un repositorio no tiene absolutamente nada ejecutable — ningún comando de pruebas, ningún
linter, nada instalado — la verificación lo dice y la respuesta se libera, y TMT tiene que
decirte con claridad que el trabajo está sin verificar. «No he podido verificar esto» es útil;
«verificado» cuando no se ejecutó nada no lo es.

## Revisión independiente

**Pídela con `/review` en tu petición.** Sin ese comando no se inicia ningún revisor y nada
de aquí condiciona la respuesta. Consulta
[Capacidades](#capacidades-plan-review-verify).

La verificación y la revisión responden a preguntas distintas, y un cambio de entidad necesita
las dos. La verificación pregunta *¿esto pasa comprobaciones ejecutables?*; la revisión pregunta
*¿es este el cambio correcto, y es seguro?*. Una suite en verde dice que el código hace lo que
dicen sus pruebas; no dice que las pruebas sean las pruebas correctas, y no se da cuenta de que
construiste la funcionalidad equivocada.

TMT revisa su propio trabajo antes de tener permiso para decir que está hecho, y no
preguntándose a sí mismo. Un **agente aparte** lee el repositorio, el diff y tu petición
original, sin haber escrito nada de ello, e informa de lo que encontró. El agente principal
tiene que actuar sobre los hallazgos bloqueantes, y el motor de ejecución no le deja responder
hasta que una revisión haya pasado de verdad.

La clave es el fallo que una suite de pruebas en verde no detecta:

> Pediste autenticación con soporte de tokens de refresco. Las pruebas pasan. El revisor lee
> el diff y encuentra que a los tokens de refresco nunca se les comprueba la caducidad, y que
> `/health` ha quedado discretamente detrás de la autenticación. Ninguna de las dos cosas está
> probada, porque el mismo agente escribió el código y las pruebas.

### El ciclo

```
      your request
           |
      plan --> implement --> tests
           |
      independent review
           |
   +-------+--------+
   |                |
 PASS            FINDINGS
   |                |
   |          main agent fixes
   |                |
   |            tests again
   |                |
   +-------<---- review again
           |
     plan complete
           |
      final answer
```

### Qué ve el revisor, y qué no

Se le entrega la **petición original del usuario** —tratada como la fuente de verdad—, el plan
que escribió el agente que implementó, el estado de finalización del plan, `git status`,
`git diff`, `git diff --stat`, el commit actual, las rutas que realmente se escribieron y una
nota de lo que realmente se ejecutó en la sesión. Todo lo demás se lo trae él mismo: los
archivos cambiados, el código que los llama, las pruebas, las convenciones propias del
proyecto. El diff va primero y el resto se expande solo donde el diff no puede responder a la
pregunta.

Es de **solo lectura**, impuesto en el código en lugar de pedido en su prompt: todos los verbos
de escritura se rechazan antes de despacharse. Él informa; el agente principal hace todos los
cambios. Tampoco puede ejecutar nada, así que no puede revisar el resultado de su propia
ejecución: lo que ejecutó la sesión se le declara como un hecho observado, y juzgar qué
demuestra eso es su trabajo.

Se le dice, con todas las letras, que no se fíe de las pruebas, que no se fíe del plan y que no
se fíe del relato que hace el agente que implementó sobre su propio trabajo.

### Hallazgos

Cada hallazgo lleva una gravedad, un archivo, una línea allí donde se leyó una de verdad, cuál
fue la evidencia, por qué importa y una indicación para arreglarlo.

| Gravedad | Bloquea la finalización |
|---|---|
| `CRITICAL` | sí — corrección, seguridad, pérdida de datos, comportamiento destructivo |
| `MAJOR` | sí — un fallo real, un comportamiento obligatorio ausente, una regresión probable, una prueba importante que falta |
| `MINOR` | no — un defecto menor que merece la pena arreglar |
| `SUGGESTION` | no — opcional |

Junto a los hallazgos, el revisor devuelve una **lista de requisitos** construida a partir de
tu petición, cada uno marcado como satisfecho, parcial o no satisfecho. Esa es la parte que
distingue «¿tiene buena pinta este código?» de «¿construimos lo que se pidió?», y es la
comprobación que detecta una implementación impecable de la funcionalidad equivocada.

El veredicto es `PASS`, `PASS_WITH_WARNINGS` o `FAIL`. Una respuesta que afirma haber pasado
mientras enumera un hallazgo bloqueante se registra como **FAIL**: deciden los hallazgos, y la
afirmación del propio revisor se muestra al lado para que la contradicción quede visible.

### Qué impide que se haga trampa

- **Solo una revisión real puede producir un aprobado.** No hay ninguna clave en ninguna acción
  que fije un veredicto. Lo único que mueve el estado de la revisión es la salida del propio
  agente revisor, analizada y validada campo a campo. Un modelo que escribe «review passed»
  escribe una frase, y las frases no mueven la máquina de estados.
- **Una revisión que no se completó no es un aprobado.** Un revisor que se cayó, agotó el tiempo
  o devolvió algo ilegible deja la tarea en `ERROR`, lo cual bloquea la respuesta final
  exactamente igual que un fallo.
- **Una revisión aprobada caduca cuando el código se mueve debajo.** Edita un archivo después de
  que una revisión pasara y la siguiente respuesta necesita una nueva: lo que pasó no es lo que
  se entregaría.
- **El paso de revisión del plan no se puede tachar antes de tiempo.** Marcar como completado un
  paso cuyo título nombra la revisión se rechaza mientras la revisión no haya pasado.
- **Un revisor no puede revisar su propio trabajo.** `review` se rechaza a todos los agentes en
  segundo plano, incluido el revisor, y el verbo solo está documentado en el prompt del agente
  principal.
- **El revisor no puede iniciarse mientras haya trabajadores en marcha.** Estarían escribiendo
  en el árbol que él está leyendo, y la revisión sería de un estado que nunca existió.

### Cuándo ocurre

Una revisión es obligatoria cuando el motor de ejecución ha visto **las dos** mitades de un
trabajo de entidad: un plan de tres o más pasos, y al menos un archivo escrito de verdad.
Ninguna de las dos por sí sola cuenta: un plan largo que no cambió nada era investigación, y un
parche de una línea sin plan era un favor. Ambas son hechos que TMT observó y no afirmaciones
que hizo el modelo, así que un modelo no puede describir su trabajo como pequeño para evitarla.

Puedes anularlo en cualquiera de los dos sentidos con tus propias palabras. «…and review the
changes» activa una; «no review needed» la desactiva. No decir nada deja la decisión en manos
de la evidencia, que es el caso habitual.

### Límites

Hay **tres ciclos de revisión por tarea**. Si el tercero sigue informando de problemas
bloqueantes, la respuesta se libera en lugar de retenerse para siempre: retenerla más gastaría
el turno y terminaría sin respuesta alguna. Sale llevando una línea que dice con claridad que la
revisión no pasó y cuántos hallazgos quedan abiertos, y el agente principal está obligado a
decir lo mismo con sus propias palabras. El silencio sería el peor de los fallos.

### En pantalla

La revisión se sitúa bajo el plan en la misma columna derecha, en tres filas como mucho:

```
PLAN 2/5                       PLAN 5/5
S1 + Inspect repository        S1 + Inspect repository
S2 + Implement feature         S2 + Implement feature
S3 > Add tests                 S3 + Add tests
S4 - Independent review        S4 + Independent review
S5 - Final verification        S5 + Final verification

REVIEW 1/3                     REVIEW 2/3
> Running independent review   + Review passed
```

Cada estado lleva una marca además de un color, de modo que la columna se lee con las secuencias
de escape eliminadas y en una terminal sin color alguno. `/review` imprime los hallazgos al
completo, y es la vía de entrada en una ventana demasiado estrecha para dos columnas.

## Contexto del proyecto: `TMT_Context/`

Antes, una sesión empezaba de cero. Todo lo averiguado sobre un proyecto —dónde está el punto de
entrada, qué comando ejecuta las pruebas, qué se implementó la semana pasada y qué quedó a
medias— se reconstruía desde cero en cada arranque, o se perdía cuando terminaba el proceso.

Ahora TMT mantiene dos archivos markdown en el proyecto sobre el que trabaja:

```text
my-project/
├── src/
├── tests/
├── package.json
└── TMT_Context/
    ├── notes.md      how this project works
    └── progress.md   what has been done, what is being done, what remains
```

Son archivos corrientes. Ábrelos, léelos, edítalos, confírmalos.

### Cuándo se crea

En la **primera tarea real** de un proyecto, no al lanzar TMT ni al escribir un comando de
barra. Arrancar y cambiar de idea no deja nada atrás.

```text
tmtcode
   ↓
"Add a dark mode to this application."
   ↓
no TMT_Context → create it, inspect the project, record what was found
   ↓
start working
```

Si `TMT_Context/` ya está ahí **no** se vuelve a crear. Si cualquiera de los dos archivos ya
está ahí **no** se sobrescribe: ni se fusiona, ni se regenera, ni se toca en absoluto.

### `notes.md` — cómo funciona el proyecto

Se escribe primero a partir de una inspección real del repositorio, y después se corrige y se
amplía a medida que TMT aprende más. Tiene encabezados estables para que tanto tú como TMT
podáis encontrar las cosas:

```text
# TMT Project Notes
## Project Overview      ## Dependencies
## Architecture          ## Constraints
## Important Files       ## Known Issues
## Build                 ## TMT Notes
## Testing
## Configuration
```

La inspección inicial es deliberadamente superficial: manifiestos, marcadores, scripts de
paquete, objetivos de Makefile, secciones `[tool.X]`, configuración de CI, el primer párrafo del
README, los directorios de primer nivel y cualquier punto de entrada que un manifiesto *declare*.
Lee archivos y no ejecuta nada, así que cuesta un instante en lugar de un recorrido de un
repositorio grande.

**Nada de lo que contiene está inventado.** Allí donde un hecho no se pudo establecer, el
archivo lo dice:

```text
## Build

Build command has not yet been confirmed. Nothing in this repository
names one that TMT recognised.
```

Esa es toda la regla. Un comando de compilación adivinado es peor que una sección vacía, porque
te cuesta un comando fallido y tu confianza en todas las demás líneas.

### `progress.md` — qué ha ocurrido

```text
# TMT Project Progress
## Current Status        ## Verification
## Completed             ## Important Decisions
## Currently Working On  ## Known Issues
## Remaining             ## Next Steps
## Tests
```

**Una tarea solo se registra como completada cuando hay evidencia de ello**: un plan cuyos pasos
terminaron todos, o archivos que se escribieron de verdad. Una tarea que terminó sin hacer nada
se queda bajo *Currently Working On* como un asunto abierto. La intención de TMT de implementar
algo nunca se convierte en una marca de visto.

**Los resultados de pruebas son siempre reales.** La sección Tests se escribe a partir de un
resultado de verificación construido con códigos de salida, así que dice `39 passed, 3 failed`
cuando eso es lo que ocurrió, y no dice absolutamente nada cuando no se ejecutó nada.

### Cómo se usa

El contexto se pone en el prompt del modelo **antes** de la instantánea del espacio de trabajo,
de modo que lo primero que lee TMT es lo que ya había averiguado y lo segundo es el repositorio
en sí. Esa es toda la idea: la segunda tarea de un proyecto debería ser más rápida que la
primera, porque no hay que redescubrir la disposición.

```text
"Now add keyboard shortcuts."
   ↓
load TMT_Context → already knows the architecture, the test command,
                   what dark mode did, what is outstanding
   ↓
search only where it still needs to
```

Va presupuestado en lugar de pegado entero. Los archivos pequeños entran completos; los grandes
conservan las secciones que más importan y **dicen cuáles se dejaron fuera**, de modo que al
modelo nunca se le enseña que una sección que no puede ver no existe.

### El contexto nunca está por encima del código

`TMT_Context/` es memoria, no verdad. Si `notes.md` dice que la autenticación vive en `auth.py`
y el repositorio la ha movido a `authentication/service.py`, **el repositorio tiene razón**.

TMT compara las rutas que nombran las notas con las rutas que existen, y pone lo que encuentra
en el prompt junto a las propias notas:

```text
STALE: these paths are named in the notes but are not in the workspace now:
src/main.py. Do not act on them without checking.
```

No edita la nota por ti: un archivo puede faltar porque la nota está desactualizada o porque
estás a mitad de una refactorización, y solo leer el código distingue las dos cosas. Lo que sí
hace es impedir que la nota se crea, y corregirla en cuanto sabe más.

### Tus ediciones están protegidas

Puedes reescribir cualquiera de los dos archivos a mano cuando quieras. TMT cambia **una sección
cada vez** y vuelve a escribir todos los demás bytes exactamente como los leyó, incluidos
encabezados de los que nunca ha oído hablar, tu prosa y tus espacios. No hay ninguna operación
que entregue un archivo entero, ni forma de pedirla.

Si editas un archivo mientras TMT trabaja, tu edición sobrevive: el archivo se vuelve a leer en
el momento del cambio en lugar de tomarse de una copia leída antes.

### Los secretos nunca se escriben

Ni claves, ni tokens, ni contraseñas, ni valores sacados de un `.env`. TMT registra el requisito
y no el valor:

```text
## Configuration

Environment variables this project declares (names only -- values are
never recorded here), from `.env.example`:

- `API_KEY`
- `DATABASE_URL`
```

El `.env` real no se abre nunca: su existencia se anota a partir de un stat y su contenido no se
lee. Todo lo que se escribe en cualquiera de los dos archivos pasa además por el mismo filtro de
credenciales que usa `remember`, y todo lo que tenga forma de credencial se redacta y se informa.

### Con `/plan`, `/review` y `/verify`

El contexto no duplica esos sistemas; recuerda lo que concluyeron.

| | va a |
|---|---|
| los pasos del plan y su estado real | `progress.md`, *Currently Working On* |
| qué ejecutó realmente la verificación, y sus números | `progress.md`, *Tests* y *Verification* |
| hallazgos de revisión bloqueantes todavía pendientes | `progress.md`, *Known Issues* |

El estado final se persiste **después** de que las barreras de finalización hayan dado su
conformidad y antes de que salga la respuesta, de modo que el orden se mantiene:

```text
work → verify → review → persist → end
```

Persistir no puede liberar nunca una respuesta que el plan, la verificación o la revisión
estuvieran reteniendo: no se le pide hasta que las tres ya han dado su conformidad. Y
`send_message` no finaliza nada: decir «he implementado la funcionalidad» es una frase, no una
evidencia.

### Por proyecto, siempre

La ruta del contexto se deduce del espacio de trabajo activo cada vez que se pide, de modo que
dos proyectos no pueden compartir uno nunca y la información de uno no puede aparecer nunca en
el otro.

### Cómo desactivarlo

Settings → **Project Context** → Enter. Por defecto está **activado**.

Con él desactivado, TMT no crea, ni lee, ni actualiza ningún `TMT_Context`. **Los archivos ya
escritos se dejan exactamente como están**: pertenecen al proyecto y a quien los escribiera, y
un ajuste no es consentimiento para borrar las notas de nadie.

El interruptor se guarda en `.tmt_context` junto a los demás ajustes por instalación de TMT, así
que es estado propio de TMT y no sigue al espacio de trabajo. Los *archivos* de contexto son lo
único que TMT escribe deliberadamente dentro de tu proyecto.

### Si no se puede crear

Un clon de solo lectura, un fallo de permisos, un disco lleno: TMT lo dice una vez y continúa con
tu tarea.

```text
Persistent project context could not be created (PermissionError: ...).
Continuing without TMT_Context.
```

Nada de esta funcionalidad merece que una tarea falle.

### ¿Debería confirmarse?

Eso lo decides tú. TMT no toca tu `.gitignore`. Por defecto trata los dos archivos como
documentación corriente del proyecto —son legibles, comparables y útiles para todo el que trabaje
en el repositorio—, así que confirmarlos comparte lo que TMT ha aprendido con el resto del
equipo. Ignóralos si prefieres que se queden en local.

## Agentes en segundo plano

TMT puede delegar. El agente principal lanza trabajadores en segundo plano, estos hacen trabajo
real a través de las mismas acciones y los mismos modelos que usa él, y él los espera e informa
de lo que hicieron.

```
Task> spawn three agents to write multiply.py, divide.py and power.py, then wait for them
```

| | Se ejecuta en | Puede editar | Puede hacer push | Habla contigo | Termina con |
|---|---|---|---|---|---|
| agente principal | el bucle de la sesión | sí | sí | sí | `end_conversation` |
| trabajador | un hilo en segundo plano | sí | **no** | no | `internal_response` |
| agente de notas | un hilo en segundo plano | **no** | no | solo la respuesta | `internal_response` |

**Diez trabajadores a la vez.** El agente principal no cuenta para ese límite, y el agente de
notas tampoco. Una undécima petición se rechaza con una frase que lo dice, no se ignora.

`/agents` imprime qué están haciendo. En una terminal de verdad, la flecha derecha al final de
una línea vacía abre lo mismo como panel en vivo, y la izquierda lo cierra.

### El contrato de delegación

Una delegación es un contrato, no un deseo. `spawn_agent` acepta un objeto `constraints`
opcional que dice qué puede hacer ese agente, cuánto tiempo puede ejecutarse y qué debe informar,
y **TMT impone las tres cosas por sí mismo.** Al agente se le comunica su contrato, y además se
le rechaza en el despachador, así que no puede sortear nada de ello eligiendo otra herramienta.

```json
{"action": "spawn_agent",
 "task": "Investigate how authentication is put together in this repository.",
 "constraints": {
     "read_only": true,
     "timeout_seconds": 600,
     "report": {"file_list": true, "diff": true, "summary": true}
 }}
```

Cada parte es opcional, y **un `spawn_agent` sin `constraints` se comporta exactamente como
siempre**: el mismo prompt, byte a byte, los mismos permisos, el mismo informe. Nada de una
delegación existente ha cambiado.

Las restricciones son **por delegación**. El trabajador n.º 1 puede ser de solo lectura con cinco
minutos mientras el n.º 2 escribe con libertad con quince; ninguno puede ver ni afectar al
contrato del otro, porque un contrato es un objeto inmutable colgado de un registro y no hay
ninguna variable global en ese camino.

#### `read_only`

`read_only: true` significa que el agente puede inspeccionar este espacio de trabajo y no puede
cambiarlo. Conserva todos los verbos de lectura — `read_file`, `read_lines`, `list_files`,
`glob`, `grep`, `find_symbol`, `tree`, `code_map`, `related_tests`, `recall`,
`git_status`, `git_diff`, `git_identity` — y se le rechaza todo lo demás.

**Se impone en tiempo de ejecución, no se pide en el prompt.** El rechazo ocurre antes de que la
acción se ejecute, en dos sitios: `agent_worker` comprueba cada acción antes de despacharla,
tanto en el camino de acción individual como en el de lotes, y `agent_actions.execute_action`
vuelve a comprobar en el despachador. Los dos preguntan a la misma función, así que hay una regla
y dos sitios que la aplican.

**Es una lista blanca, no una lista de verbos prohibidos.** Toda acción añadida a TMT después de
escribir esto se rechaza por defecto. Una lista de verbos prohibidos admitiría en silencio el
siguiente verbo de mutación que alguien registrase, y quien lo añade no es quien escribió la
lista.

Eso cubre los caminos que no son escrituras de archivo evidentes:

| Rechazado | Porque |
|---|---|
| `write_file`, `append_file`, `write_files`, `patch_file`, `replace_lines`, `replace_across`, `copy_file`, `rename_file`, `create_folder`, `delete_file`, `delete_folder` | cambian archivos |
| `run_file` / `run_python` | un programa puede escribir cualquier cosa, así que ejecutar uno es un camino de mutación |
| `git_commit` | confirmar cambia el repositorio |
| `open_app` | lanza una aplicación fuera del espacio de trabajo |
| `remember` | escribe en el almacén de memoria propio de TMT |
| `git_push`, `plan`, `review`, `verify`, `project_context` | ya se rechazan a todos los agentes en segundo plano, haya contrato o no |

**TMT no tiene ningún verbo de shell general**, por lo que aquí no hay ninguna lista de comandos
«seguros» en ninguna parte. La única forma en que un trabajador puede ejecutar código arbitrario
es `run_file`, y a una delegación de solo lectura se le rechaza sin más: sin analizar cadenas de
comandos, sin adivinar si `sed -i` escribe. Esa es la versión honesta de la garantía: se apoya en
que haya un único camino de ejecución y en que esté cerrado, no en que TMT sea capaz de distinguir
un comando que muta de uno inofensivo.

**Lo que no afirma.** Una delegación de solo lectura no puede hacer un cambio persistente a través
de ningún verbo que TMT le ofrezca. No es un entorno aislado: TMT no está impidiendo escrituras a
nivel del sistema operativo, y si alguna acción futura abriera una vía habría que añadirla a la
lista blanca deliberadamente antes de que un trabajador de solo lectura pudiera alcanzarla.

Un intento rechazado se **informa, no se oculta**. Al trabajador se le dice qué se bloqueó y por
qué, para que pueda ajustarse y continuar —una escritura bloqueada no es automáticamente una
delegación fallida— y el intento queda registrado y llega al agente principal en el resultado:

```
Constraint violations: 1 write operation blocked (write_file src/auth.py)
```

#### `timeout_seconds`

Un número entero de 1 a 3600. Es el tiempo máximo de ejecución de **toda la delegación**, no de
una acción, y no se reinicia porque termine una acción o porque responda el modelo.

El reloj arranca cuando el trabajador realmente empieza, no cuando se registra, así que una
delegación nunca pierde parte de su tiempo porque otra cosa vaya lenta.

**Lo impone el motor de ejecución.** El plazo se comprueba en los mismos tres límites que la
cancelación: al principio de cada paso, entre fragmentos de una respuesta transmitida en streaming,
y en la línea inmediatamente anterior al despacho de cada acción. Cuando se cumple:

- no se ejecuta ninguna acción más;
- el estado del agente pasa a `timed_out`, que **no** es `failed` ni `killed`;
- su plaza de trabajador se libera de inmediato, así que una delegación que estuviera esperando
  capacidad puede empezar;
- se conserva lo que hubiera hecho, y el informe que debiera se sigue recopilando.

La garantía es exactamente la que lleva `kill` y ninguna mayor: **pasado el plazo, no se despacha
ninguna llamada de herramienta más.** Una petición que ya está en vuelo termina de llegar, porque
un hilo de Python no se puede terminar y una respuesta en streaming no tiene primitiva de aborto.
Afirmar una terminación instantánea sería mentir en el único sitio donde una mentira sale cara.

No hay ningún hilo temporizador. El plazo es aritmética sobre el registro, barrida siempre que la
respuesta pueda importar: antes de cada comprobación de capacidad, en cada repintado y dentro de
cada espera, que nunca se bloquea más allá del plazo más cercano. Es el mismo diseño que usa la
retención de cinco segundos de las tarjetas, y por las mismas razones: nada que cancelar, nada que
se filtre, y una prueba lo ejercita avanzando un número en lugar de esperar diez minutos.

Los tiempos límite inválidos se rechazan antes de que empiece nada: uno negativo, un cero, una
cadena, un `true`, una fracción de segundo o cualquier cosa por encima del techo de una hora. **Un
contrato rechazado no inicia ningún trabajador**: una delegación ejecutándose bajo medio contrato
es el único resultado sobre el que nadie puede razonar.

#### `report`

`file_list`, `diff` y `summary`, cada uno por separado. **No son permisos** y nunca afectan a lo
que el agente puede hacer.

- **`file_list`** — los archivos que las propias acciones del agente leyeron y escribieron de
  verdad, tomados de las peticiones que esas acciones llevaban. Nunca se arma a partir de nada que
  el agente dijera sobre lo que había leído.
- **`diff`** — lo que dice git sobre los archivos que este agente escribió. Acotado a esos archivos
  deliberadamente: el agente principal sigue trabajando mientras corre un trabajador y pueden correr
  varios a la vez, así que el diff del árbol entero no es en absoluto el trabajo de una delegación.
  El diff de una delegación de solo lectura dice `No changes permitted by delegation.` El de una con
  permiso de escritura que no cambió nada dice `No workspace changes.`
- **`summary`** — el relato que hace el agente de su propio trabajo, que es el `response` de su
  `internal_response` y la única parte del informe que son palabras del modelo.

Los informes se recopilan en **todos** los finales, no solo en los exitosos: una delegación agotada
por tiempo o cancelada sigue teniendo una lista de archivos real, un diff real y tiempos reales, y
tirar eso porque no terminó con normalidad descartaría el único registro de lo que logró hacer.

Lo que le vuelve al agente principal es estructurado y conciso: sin transcripciones de llamadas de
herramienta, sin registros en bruto:

```
Background agent #4
STATUS: TIMED OUT
Contract: READ ONLY  TIMEOUT 10:00  FILES  SUMMARY
Runtime: 10:00 of 10:00
Progress: 17 actions taken, 11 files inspected, 0 files changed

SUMMARY
  Found the authentication entry point in AuthService; three test modules cover it.

FILES
  Inspected (11):
    src/auth/service.py
    src/auth/token.py
    ...
  Changed: none
```

#### En pantalla

El contador junto al indicador dice `4/10 agents`, la cabecera del panel dice `AGENTS 4/10`, y la
tarjeta de un agente con restricciones lleva su contrato de forma compacta:

```
██░░░░░░ #3  RO  8:32/10:00  +0 -0  ~4k out  1m28s  running
```

`RO` es solo lectura, el par es el tiempo restante frente al límite —una cuenta atrás real hecha con
la misma aritmética que de verdad detendrá el trabajo— y `F D S` en la tarjeta marca los requisitos
del informe. `/agents` lo dice todo al completo, donde hay ancho para leerlo. Un agente agotado por
tiempo dice `timeout` y se colorea como una parada y no como un fallo, porque eso es lo que es.

#### Delegación anidada

Los agentes en segundo plano no pueden lanzar agentes propios —su contexto de acciones no lleva
registro alguno—, así que una delegación de solo lectura no tiene forma de alcanzar una con permiso
de escritura. Eso ya era cierto antes de que existieran los contratos y no ha cambiado; aquí no se
inventa ninguna regla de trabajadores anidados para algo que no puede ocurrir.

### Verlos trabajar

Mientras hay agentes en marcha, cada uno recibe una fila propia justo debajo de la barra de progreso
principal:

```
██████████  60% Working                      <- the main agent, in colour
██░░░░░░ #1  +45 -3  4k out  47s  running    <- one row per agent, in grey
██████░░ #2  +0 -0  ~900 out  1m34s  running
████████ #3  +7 -120  ~15k out  2m21s  done
```

Cada fila lleva el número del agente, las líneas que ha añadido y quitado, los tokens que ha
generado, cuánto tiempo lleva trabajando y su estado. Todo lo que hay en ella está medido y no
estimado, salvo donde una cifra lleva `~`: eso significa que el proveedor no la informó y TMT la
dedujo del texto, y se marca en todos los sitios donde ocurre.

**Las barras de los agentes son grises y la principal va en color, y esa es toda la razón de la
diferencia.** El degradado de color significa «el agente principal está trabajando, y esto es lo
lejos que ha llegado». Cinco barras en color se leerían de un vistazo como un proceso informado
cinco veces. Los agentes reciben la ausencia de color en lugar de un color propio.

**La barra de un agente muestra la parte de su presupuesto de pasos que ha gastado, no lo cerca que
está de terminar.** Nadie puede saber lo segundo: una barra que lo insinuara estaría inventando la
única cifra que nadie tiene. La barra de un agente terminado está llena porque ha acabado, que es el
único momento en que la finalización sí se conoce.

La fila y la tarjeta de un agente terminado se quedan cinco segundos y después desaparecen. Su
resultado no: el agente principal puede pedirlo mucho después.

El contador que hay encima del cuadro de entrada suma el trabajo de los agentes a los totales de la
propia sesión:

```
+55 lines, -5 lines, ~12k context, 433 out, agents ~22k tokens
```

Las líneas incluyen todo lo que escribieron los agentes: una línea que escribió un trabajador es una
línea que escribió la sesión, y un contador que dijera `+0` mientras cinco trabajadores reescriben
el proyecto estaría diciendo la verdad sobre un hilo y una mentira sobre la sesión. El gasto de
tokens de los agentes se informa aparte de `context`, porque ese es lo llena que está la ventana de
la petición en vuelo, y meter cinco trabajadores dentro describiría un contexto que no existe.

### `/note` — preguntar sobre el espacio de trabajo sin molestar a nada

```
Task> /note which module owns the prompt box?
```

Un agente de solo lectura responde desde el espacio de trabajo mientras todo lo demás continúa.
Puede buscar, leer e inspeccionar la estructura; no puede crear, editar, borrar ni hacer push, y eso
se impone con una lista blanca comprobada antes de cada acción y no pidiéndoselo por favor.

La pregunta va en la misma línea. Esa forma funciona en todas partes, incluida una ejecución
canalizada: el lector canalizado toma una tarea por línea, así que un indicador en dos fases no se
puede alcanzar desde una tubería en absoluto. En una terminal de verdad, un `/note` a secas pedirá
la pregunta aparte.

### Lo que los agentes en segundo plano deliberadamente no pueden hacer

Son límites del diseño, no cosas dejadas a medias:

- **Un trabajador no puede hacer push.** Puede leer `git status`, `diff`, `log` y `branch`, y puede
  hacer commit; llegar a un remoto se queda con el agente principal, que necesita tus propias
  palabras en la tarea para poder hacerlo.
- **Un trabajador no puede borrar un archivo ni una carpeta.** Ambas cosas esperan a que un humano
  confirme en la terminal, y un hilo en segundo plano no tiene terminal donde preguntar. En su lugar,
  un trabajador informa de la ruta y lo hace el agente principal.
- **Un trabajador no puede ejecutar la suite de pruebas.** `run_file` se rinde a los 10 segundos y
  una suite real tarda más, así que a un trabajador al que se le pide verificar pruebas dice que no
  pudo y qué hizo en su lugar. No informará de un resultado que nunca vio.
- **«Matar» es cooperativo, no instantáneo, y lo mismo un tiempo límite.** Python no puede detener un
  hilo por la fuerza. Lo que está garantizado, y lo que está probado, es que **no se ejecuta ninguna
  llamada de herramienta más una vez que un agente ha sido matado o ha pasado su plazo**: la
  cancelación surte efecto en el siguiente fragmento o en el siguiente límite de acción. Un agente
  atascado en una conexión colgada se marca como matado y se abandona; su hilo es un demonio y no
  puede mantener a TMT abierto jamás.
- **No hay cola.** Diez trabajadores es un tope duro y la undécima petición se rechaza con una frase,
  no se aparca. TMT no tiene ningún planificador con el que integrarse y construir uno para esto sería
  bastante más grande de lo que el tope necesita; el rechazo nombra el tope y dice qué hacer al
  respecto, que es sobre lo que actúa el agente principal.
- **Esperar bloquea al agente principal.** Es una acción corriente, no una suspensión. La interfaz
  sigue viva mientras espera porque la región en vivo se repinta en su propio hilo, y Ctrl-C te
  devuelve al indicador.
- **Los trabajadores no coordinan sus escrituras.** Cualquier escritura individual es atómica, y si
  dos trabajadores tocan el mismo archivo se le dice al agente principal cuáles. No hay más bloqueo
  que eso, así que dale a los trabajadores concurrentes archivos separados.
- **Nunca ves las acciones propias de un trabajador.** La interfaz muestra una barra y una etiqueta
  corta por cada uno, no las lecturas y ediciones que está haciendo. Lo que hizo vuelve en el resumen
  del agente principal, que es la razón por la que al agente principal se le dice que cuente qué
  delegó.
- **Una tarjeta no muestra tiempo transcurrido; la fila bajo la barra de progreso sí.** El panel se
  repinta solo cuando cambia su contenido, y una duración dibujada ahí o quedaría obsoleta o forzaría
  un repintado en cada tic, que es lo que hacía parpadear el cursor.

### Lo que cuestan los agentes

Cada trabajador lleva su propio prompt de sistema en cada petición, porque la API no tiene estado. Ese
prompt son unos 14k tokens estimados frente a los 19k del agente principal: lleva un `tree` del
proyecto en lugar del contenido de los archivos que el prompt principal incrusta, lo que ahorra
aproximadamente 1.500 tokens por petición. Diez trabajadores llevan cada uno una copia, así que
delegar no es gratis: compra paralelismo con tokens, y subir el tope de cinco a diez duplicó cuánto
puede comprar una sesión de golpe. Delega trabajo que sea genuinamente separable, no trabajo que
podrías hacer tú mismo en dos pasos.

Un contrato añade unos cuantos cientos de tokens al trabajador que lo lleva, y nada en absoluto a un
trabajador que no lo lleva: el prompt de una delegación sin restricciones es byte a byte el que era
antes de que existieran los contratos.

## Interfaz

Mientras corre una tarea: una animación THINKING hasta la primera salida, y después una barra de
progreso, el tiempo transcurrido y un contador de tokens en vivo. El texto del modelo se revela
carácter a carácter según llega. La respuesta final va en un recuadro. Un recuento de agentes en
marcha aparece junto al medidor siempre que haya alguno.

**El panel de agentes es una columna al pie de la pantalla, no una barra lateral de altura
completa.** Comparte la región en vivo con la respuesta y el cuadro de entrada; la conversación
que hay encima conserva el ancho completo y no se redibuja nunca. Es un límite deliberado y no uno
por terminar: el historial de desplazamiento es el único registro permanente que TMT tiene de una
sesión, y las dos secuencias de escape que permitirían a un programa adueñarse de toda la ventana
—estrechar la región de desplazamiento y el búfer de pantalla alternativa— lo destruyen. Las líneas
que salen por desplazamiento de una región estrechada se descartan en lugar de conservarse, así que
desplazarse hacia arriba dejaría de llegar al historial de la propia sesión. Una prueba busca en los
módulos para impedir que cualquiera de las dos vuelva.

En una terminal de menos de 45 columnas el panel ocupa todo el ancho de la región en vivo y el cuadro
de entrada no se dibuja mientras está abierto; por debajo de 30 columnas se niega a abrirse y dice por
qué. Las tarjetas descartan su línea de actividad antes que su línea de tokens, y truncan en lugar de
ajustar el texto.

### Escribir mientras trabaja

El cuadro de entrada permanece activo durante todo un turno. Puedes escribir la siguiente pregunta
mientras el agente aún trabaja en la anterior, con teclas de edición y todo.

**Enter pone la línea en cola en lugar de interrumpir.** Se responde en cuanto termina la tarea
actual, y las líneas se responden en el orden en que las introdujiste, de modo que puedes apilar tres
seguimientos e irte. El cuadro dice cuántos están esperando.

`/note` también se puede escribir ahí, que es justamente para lo que sirve: responde desde el espacio
de trabajo sin molestar al trabajo en curso.

Ctrl-C sigue deteniendo la tarea en marcha, exactamente igual que antes.

Esto necesita una terminal de verdad. Una ejecución canalizada o redirigida lee una tarea por línea y
el cuadro queda inerte, que es lo que obtienen todas las ejecuciones automatizadas y la suite de
pruebas.

Define `TMT_STREAM=0` para desactivar el streaming. El streaming necesita además `requests`; sin él
TMT funciona sin streaming.

## Comandos de barra

En el indicador, una línea que **no es más que** un comando `/` la responde el propio TMT y nunca se
envía al modelo. Los nombres no distinguen mayúsculas de minúsculas. Todo lo demás es una tarea y va
al modelo exactamente igual que antes, incluida una línea que simplemente empieza por una ruta, como
`/usr/bin/python is broken`.

`/plan`, `/review` y `/verify` son los tres que se leen de las dos maneras. Solos en la línea, cada uno
es el informe de solo lectura de más abajo; con una tarea detrás — `/plan Build the login page` — la
línea es esa tarea, con la capacidad activada para ella. Consulta
[Capacidades](#capacidades-plan-review-verify).

| Comando | Qué hace |
|---|---|
| `/context` | la conversación hasta ahora: modelo, proveedor, espacio de trabajo, cuántos turnos se arrastran a la siguiente petición, tokens estimados de entrada y de salida, líneas añadidas y quitadas, y las últimas preguntas |
| `/config` | los ajustes bajo los que se ejecuta una petición: modelo, proveedor, esfuerzo, streaming, modo JSON, espacio de trabajo, y si hay una clave de API configurada |
| `/clear` | olvidar la conversación y empezar de cero. El modelo, el esfuerzo, el espacio de trabajo y todos los demás ajustes se conservan, y no se toca ningún archivo |
| `/effort` | mostrar el nivel de esfuerzo actual |
| `/effort low\|medium\|high` | establecerlo |
| `/model` | mostrar el modelo actual y los que ofrece este proveedor |
| `/model <name>` | cambiar a uno, por id o por el nombre que se muestra en Settings |
| `/note <question>` | responder una pregunta sobre el espacio de trabajo sin cambiar nada |
| `/notes` | qué recuerda TMT de este proyecto entre sesiones: dónde está `TMT_Context/`, qué hay en cada archivo, y qué notas nombran rutas que ya no existen |
| `/agents` | qué están haciendo los agentes en segundo plano |
| `/back` | salir al menú de inicio conservando la sesión. Ver más abajo |
| `/plan` | los pasos que TMT está recorriendo para esta tarea, y lo que queda |
| `/verify` | qué se ejecutó realmente para comprobar el trabajo de esta tarea: cada comprobación, su comando, su código de salida y la salida de todo lo que falló |
| `/review` | qué encontró la revisión independiente: el veredicto, cada hallazgo y el historial de revisiones de esta tarea |

### `/back` — el menú, sin perder la sesión

`/back` vuelve a poner el menú de inicio en pantalla sobre una sesión que sigue en marcha. No se
termina, ni se borra, ni se cancela, ni se espera nada: la conversación sigue siendo la conversación,
el plan sigue siendo el plan, y cualquier agente en segundo plano sigue trabajando por detrás. Antes de
esto, Settings y Help solo se alcanzaban saliendo.

El menú que abre es el menú de inicio con tres diferencias:

```
> Resume    Go back to the session, which is still here
  Settings  Provider, API key and the model TMT runs on
  Help      What TMT does, and how to drive it
  Exit      Close TMT and end the session
```

- **Start dice Resume**, y su etiqueta sigue recorriendo el degradado incluso cuando el cursor está en
  otra parte: esa es la pantalla diciéndote que tu sesión sigue ahí.
- **Exit dice que termina la sesión.** La palabra es la misma que al arrancar; la consecuencia no.
- **Settings no se ofrece mientras haya algo en marcha.** La fila desaparece —no atenuada, no
  desactivada— y una línea encima de la lista dice qué está en marcha y qué hacer:

```
Settings are not offered while work is running: 2 agents. Wait for it to
finish, then /back again.
```

El proveedor, la clave y el modelo se leen todos mientras hay una petición en vuelo, así que cambiar uno
por debajo de un agente en marcha mete un cambio que nadie pidió en mitad de una petición que ya había
empezado. Cuando el trabajo termina, la fila vuelve en el siguiente fotograma, sin cerrar el menú.

Elegir Resume limpia la pantalla, dibuja de nuevo la cabecera y te devuelve al indicador con todo tal
como lo dejaste.

**El esfuerzo** es cuánto trabajo gastará TMT en una tarea. Cambia dos cosas, y solo cosas que son
reales en todos los proveedores: la longitud de respuesta que se pide, y cuántas rondas del bucle del
agente puede consumir una pregunta.

| Nivel | Longitud de respuesta pedida | Rondas por tarea |
|---|---|---|
| `low` | 4096 tokens | 12 |
| `medium` (por defecto) | 4096 tokens | 35 |
| `high` | 8192 tokens | 60 |

La longitud de respuesta no baja de 4096 en ningún nivel. Cada respuesta es un único objeto JSON y las
que importan llevan un archivo entero dentro, así que un límite menor no hace al modelo más escueto:
corta el objeto a mitad de cadena y la escritura no llega a ocurrir. El ajuste se guarda en
`.tmt_effort` junto a la instalación y sobrevive a un reinicio, igual que la elección de modelo.

**Autocompletado.** En una terminal de verdad, escribir `/` lista los diez comandos bajo la línea que
estás escribiendo, y se va estrechando conforme avanzas: `/mo` deja `/model`. Tab completa hasta donde
los candidatos coinciden: `/mo` se convierte en `/model `, `/co` se convierte en `/con`, porque
`/context` y `/config` siguen valiendo los dos. Una ejecución canalizada o redirigida lee una línea
entera y no dibuja ninguna lista; los comandos en sí siguen funcionando ahí.

**Secretos.** Ni `/context` ni `/config` imprimen jamás una clave, un token o una contraseña. `/config`
dice si hay una clave configurada y nada más sobre ella: ni el valor, ni una forma enmascarada de él.

## Configuración

| Variable | Valor por defecto |
|---|---|
| `OPENROUTER_API_KEY` | de `.tmt_key` |
| `OPENROUTER_MODEL` | `minimax/minimax-m3:free` |
| `TMT_STREAM` | `1` |
| esfuerzo | `medium`, de `.tmt_effort`; se establece con `/effort` |
| contexto de proyecto | activado, de `.tmt_context`; se establece en Settings. Consulta [Contexto del proyecto](#contexto-del-proyecto-tmt_context) |
| `TMT_GIT_NAME` | `TMT code` |
| `TMT_GIT_EMAIL` | ninguno — obligatorio antes de que TMT haga commit |
| `TMT_GIT_ROOT` | el repositorio que contiene el directorio del proyecto |
| el argumento `PATH`, o `--dir` | el directorio actual |

## `tmtcode` no reconocido

El comando está instalado, pero el directorio en el que pip lo puso no está en tu PATH. Localiza ese
directorio:

```bash
python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
```

Es `Scripts` en Windows y `bin` en macOS y Linux, bajo el Python o el entorno virtual en el que
instalaste. Añádelo al PATH, o usa cualquiera de las dos alternativas: ambas aceptan los mismos
argumentos y eligen el directorio del proyecto de la misma manera:

```bash
python -m TMT                     # en cualquier sitio, una vez instalado
python /path/to/TMT/TMT.py        # en cualquier sitio, directamente desde un clon
```

Si instalaste en un entorno virtual, `tmtcode` existe solo mientras ese entorno esté activo.

`tmtcode --help` imprime los argumentos.

## Pruebas

```bash
python run_tests.py
```

La suite vive en `testing/`, dividida en `testing/unit/` y `testing/integration/`. El ejecutor se queda
en la raíz y descubre las dos; consulta
[testing/README.md](../testing/README.md) para saber qué va en cada sitio.

1581 pruebas. Ocho de ellas leen la clave de API de `.tmt_key`, así que en un clon nuevo sin clave
configurada esas ocho fallan y el resto pasan.

Tarda unos quince minutos en vez de los dos que tardaba, y casi todo eso es una única prueba de
`test_agent_review.py` que arranca tres agentes revisores reales y aguanta un viaje de ida y vuelta
real a la API por cada uno. Esa es además la única prueba de aquí que no es determinista: fija una
revisión fallida y después envía objetos falsos para demostrar que ninguno de ellos puede convertirla
en un aprobado, pero ejecutar `review` ejecuta una revisión, así que un revisor en vivo al que le
guste tu árbol de trabajo la hace pasar por la vía legítima y salta la aserción. Vuelve a ejecutarla
antes de creértela.

## Licencia

Apache license 2. Consulta [LICENSE](../LICENSE).
