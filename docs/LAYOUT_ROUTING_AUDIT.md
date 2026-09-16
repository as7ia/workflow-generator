# Audit layout et routing BPMN

## Contrat

Le layout reçoit un workflow canonique déjà validé et l'analyse produite par `WorkflowGraphAnalyzer`. Il ne modifie ni le workflow ni l'analyse. Sa sortie contient exclusivement la géométrie BPMN-DI, les canaux de routing et des diagnostics. Le routing ne modifie aucun élément métier ; le générateur XML consomme les rectangles et waypoints calculés.

## Architecture du layout

Le calcul est effectué process par process. Les niveaux viennent du DAG condensé des composantes fortement connexes : les message flows sont absents de cette topologie et ne peuvent donc pas déplacer les activités d'un process. L'abscisse suit `base_x + level * pitch`, où le pitch tient compte de la plus grande largeur de node et de l'espacement horizontal.

Dans chaque groupe de lane, les composantes déconnectées occupent des bandes distinctes. Les indices de branche fournis par l'analyseur répartissent un nombre quelconque de branches autour de l'axe central. Les collisions locales sont résolues vers le bas de façon stable. Les joins sont centrés sur leurs prédécesseurs. Les nodes d'une SCC partagent leur niveau causal mais reçoivent des positions verticales déterministes. Les start events tendent donc vers la gauche et les end events vers la droite lorsque la causalité le permet.

Les participants sont actuellement empilés verticalement (`metadata.orientation = horizontal`, au sens BPMN des pools). Les lanes d'un participant sont également empilées, avec une hauteur calculée depuis leur contenu et une hauteur minimale. Un participant ou un process sans lane conserve une bande technique. La largeur des pools dépend du niveau maximal et des dimensions des nodes. Les pools, lanes et nodes font l'objet de contrôles de contenance et de chevauchement.

`detect_shape_collisions` fournit un diagnostic déterministe tenant compte de la largeur, de la hauteur et d'une marge optionnelle. `layout_metrics` expose le nombre de collisions de nodes, de back edges et de message flows.

## Architecture du routing

Le routeur calcule globalement toutes les routes dans l'ordre stable des identifiants. Chaque route est orthogonale et utilise des ports réels sur les côtés des shapes. Il évalue plusieurs familles de chemins : progression gauche-droite, liaison verticale et corridors extérieurs haut, bas, gauche ou droite. La priorité de score est : aucune shape étrangère traversée, éviter une superposition déjà utilisée, longueur, puis nombre de coudes.

Les splits et joins reçoivent des offsets de ports stables selon le rang des flows entrants/sortants. Leurs premiers et derniers segments ne sont donc pas totalement superposés. `edge_intersects_shape` teste explicitement les segments contre une shape étrangère. La recherche reste bornée à un petit ensemble constant de candidats et n'est pas exponentielle.

## Back edges

Les back edges identifiés par le `GraphAnalyzer` reçoivent chacun un `channel_index`. Le routeur utilise un corridor sous les limites du process : `process_bottom + BACK_EDGE_MARGIN * (index + 1)`. Plusieurs retours utilisent ainsi des corridors distincts et ne traversent pas directement les nodes de la boucle. La structure de la boucle n'est jamais modifiée.

## Message flows

Les message flows sont routés séparément des sequence flows. Ils utilisent des corridors externes à droite de l'ensemble des shapes. Chaque message reçoit un corridor distinct, stable par identifiant. Cette stratégie fonctionne avec le placement vertical actuel des pools et empêche les messages d'influencer les niveaux internes.

## Complexité

- Placement des nodes : approximativement `O(V²)` dans le pire cas à cause de la résolution locale de collisions, généralement proche de `O(V)` pour des niveaux espacés.
- Détection exhaustive des collisions de shapes : `O(V²)`, utilisée comme diagnostic.
- Routing : `O(E * V)` car chaque edge teste un nombre constant de candidats contre les shapes. Il n'existe ni recherche globale exhaustive ni pathfinding combinatoire.
- Allocation des corridors et déterminisme : tris `O(E log E)`.

## Limites restantes

- L'orientation des pools est volontairement fixe et documentée ; aucune bascule automatique imprévisible n'est réalisée.
- Le moteur privilégie l'absence de traversée de shapes. Les croisements ponctuels edge/edge restent possibles dans les graphes denses.
- La détection diagnostique shape/shape reste quadratique ; un index spatial ne devient utile que pour des diagrammes DI beaucoup plus grands que les charges actuelles.
- Les labels très longs ne participent pas encore au dimensionnement des shapes.

## Validation

Les tests couvrent chaîne, trois lanes, split à trois branches, split/join parallèle, SCC/cycle, plusieurs participants, process sans lane, absence de collision, routes forward/inter-lane, split, join, un et deux back edges, un et plusieurs message flows, collision edge/shape et déterminisme. Six fixtures synthétiques (`linear`, `split_join`, `three_lanes`, `loop`, `multiple_pools`, `collaboration`) génèrent du BPMN-DI XML analysable et ouvrable dans bpmn.io.
