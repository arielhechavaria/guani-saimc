#!/usr/bin/env bash
# Borra los worktrees y las ramas locales que YA están mergeados en master.
#
# Uso (desde cualquier carpeta del repo):
#   scripts/clean_worktrees.sh            # simulacro: lista qué borraría, no toca nada
#   scripts/clean_worktrees.sh --apply    # borra de verdad
#
# Qué borra: un worktree y su rama solo si la punta de la rama es ancestro de
# master (`git merge-base --is-ancestor`), o sea, si no hay nada que perder.
# Qué conserva, y lo dice: ramas sin mergear, worktrees con cambios sin
# commitear o archivos sin seguimiento, y el worktree donde estás parado.
# Tampoco toca el checkout principal ni la rama master.
#
# Limitación: un merge con squash o rebase no deja la rama como ancestro de
# master, así que esa rama se reporta como «sin mergear» y se queda. Si ya
# sabés que está integrada, borrala a mano.

set -euo pipefail

APPLY=0
case "${1:-}" in
  "") ;;
  --apply) APPLY=1 ;;
  *) echo "Uso: $0 [--apply]" >&2; exit 2 ;;
esac

BASE=master
# El primer worktree que lista git es siempre el checkout principal.
MAIN=$(git worktree list --porcelain | awk '/^worktree /{print substr($0, 10); exit}')
HERE=$(git rev-parse --show-toplevel)

if [ "$(git -C "$MAIN" symbolic-ref --short HEAD)" != "$BASE" ]; then
  echo "El checkout principal ($MAIN) no está en $BASE. Cambiá a $BASE y reintentá." >&2
  exit 1
fi

removed=0
kept=0

say() { printf '  %-9s %s\n' "$1" "$2"; }

# Recorre los worktrees secundarios (todos menos el principal).
path=""
branch=""
process() {
  [ -z "$path" ] && return
  [ "$path" = "$MAIN" ] && return
  if [ -z "$branch" ]; then
    say "conserva" "$path (HEAD suelto, sin rama)"; kept=$((kept + 1)); return
  fi
  if [ "$path" = "$HERE" ]; then
    say "conserva" "$branch (es el worktree donde estás)"; kept=$((kept + 1)); return
  fi
  if ! git -C "$MAIN" merge-base --is-ancestor "$branch" "$BASE"; then
    say "conserva" "$branch (sin mergear en $BASE)"; kept=$((kept + 1)); return
  fi
  if [ -n "$(git -C "$path" status --porcelain)" ]; then
    say "conserva" "$branch (cambios sin commitear o archivos sin seguimiento)"
    kept=$((kept + 1)); return
  fi
  if [ "$APPLY" -eq 1 ]; then
    git -C "$MAIN" worktree remove "$path"
    git -C "$MAIN" branch -d "$branch" >/dev/null
    say "borrado" "$branch  ($path)"
  else
    say "borraría" "$branch  ($path)"
  fi
  removed=$((removed + 1))
}

echo "Worktrees secundarios:"
while IFS= read -r line; do
  case "$line" in
    "worktree "*) process; path=${line#worktree }; branch="" ;;
    "branch "*)   branch=${line#branch refs/heads/} ;;
  esac
done < <(git worktree list --porcelain; echo)
process

# Ramas mergeadas que ya no tienen worktree (quedaron de uno retirado a mano).
echo "Ramas locales huérfanas:"
orphans=0
while IFS= read -r b; do
  [ "$b" = "$BASE" ] && continue
  # Si tiene worktree, ya se trató arriba.
  git -C "$MAIN" worktree list --porcelain | grep -qx "branch refs/heads/$b" && continue
  orphans=$((orphans + 1))
  if [ "$APPLY" -eq 1 ]; then
    git -C "$MAIN" branch -d "$b" >/dev/null
    say "borrada" "$b"
  else
    say "borraría" "$b"
  fi
done < <(git -C "$MAIN" for-each-ref --merged "$BASE" --format='%(refname:short)' refs/heads/)
[ "$orphans" -eq 0 ] && echo "  (ninguna)"

[ "$APPLY" -eq 1 ] && git -C "$MAIN" worktree prune

echo
if [ "$APPLY" -eq 1 ]; then
  echo "Listo: $removed worktree(s) borrado(s), $kept conservado(s)."
else
  echo "Simulacro: $removed worktree(s) se borrarían, $kept se conservan. Para borrar: $0 --apply"
fi
