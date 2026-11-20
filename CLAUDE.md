# NexusOS

Avant toute reprise, lire `HANDOFF.md` (état, règles, outils, prochaine étape) : il fait foi.
À chaque étape terminée : **réécrire** `HANDOFF.md` (état courant + prochaine étape, sous ~150 lignes ; ne pas y ajouter de
paragraphe de session), noter le récit de la session en fin de `docs/journal/journal.md` (lu seulement au besoin), commiter.
`HANDOFF.md` et `docs/journal/` sont exclus de git (dépôt public) : ne jamais les commiter ni les forcer (`git add -f`).
Rien de personnel dans les commits (machine, chemins, noms, mesures).
Agents : Sonnet = implémentation cadrée (spec + test) ; Opus = conception, specs, relecture après chaque ajout ; escalade
vers Opus seulement si tests rouges ou échec flagrant.
**Validation réelle à chaque jalon** (pas à chaque modif) : lancer le résultat et le comparer à l'oracle du projet (voir
`HANDOFF.md`, Outils et tests : `ci/test-install.sh` en QEMU UEFI + BIOS), à piloter soi-même sans attendre que
l'utilisateur le demande ; les tests ne suffisent pas.
