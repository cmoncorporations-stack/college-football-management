"""Géographie du fan fit : lieu de naissance → commune → canton → zone d'ancrage.

Les lieux de naissance Transfermarkt sont écrits dans la langue de la fiche : « Genf »
et « Genève » sont la même ville, « Biel/Bienne », « Biel-Bienne » ou « Bienne » la
même commune. Ce module normalise ces écritures (minuscules, accents, tirets, barres,
« près », « sur », « les », suffixes de canton « ZH », « SG »…) et rattache chaque
commune connue à son canton, puis à une zone :

    Bassin nord-vaudois   districts du Jura-Nord vaudois et de la Broye-Vully (indice
                          de pénétration du cockpit)                          ancrage 1,00
    Vaud                  autre commune vaudoise                              ancrage 0,85
    Romandie              GE, FR, NE, JU, VS et Jura bernois / Bienne         ancrage 0,70
    Suisse                autre commune suisse, ou pays de naissance / nationalité suisse
                                                                              ancrage 0,45
    Frontalier            communes françaises de l'Ain, du Doubs, du Jura et de la
                          Haute-Savoie proches de la frontière                ancrage 0,40
    International         tout le reste                                       ancrage 0,10

Règle du club formateur (`youth_clubs`), appliquée à l'identique à tous les joueurs,
après le lieu de naissance : un passage dans la formation d'Yverdon Sport vaut
« Bassin nord-vaudois » ; un club formateur romand (liste ROMAND_CLUBS) vaut
« Romandie ». On retient la meilleure des deux zones (naissance, formation) : un
joueur né à Genève formé à Yverdon est du bassin ; un joueur né à Zurich formé à
Sion est romand ; un joueur né à Orbe formé à Bâle reste du bassin.

Points d'entrée :
    normalize(text)                  écriture canonique d'un toponyme
    canton(city, country=None)       code canton ('VD', 'GE'…) ou None si inconnu
    zone(city, country=None, citizenships=(), youth_clubs=()) -> (zone, ancrage)
    unrecognized(players, n=20)      lieux de naissance suisses ou français non reconnus
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

SWISS_COUNTRY = {"switzerland", "suisse", "schweiz", "svizzera", "svizra"}
FRENCH_COUNTRY = {"france", "frankreich", "francia"}

ZONE_SCORE = {"Bassin nord-vaudois": 1.0, "Vaud": 0.85, "Romandie": 0.7, "Suisse": 0.45,
              "Frontalier": 0.4, "International": 0.1}
ROMAND_CANTONS = {"GE", "FR", "NE", "JU", "VS"}

# Communes → canton. Clés en écriture canonique (voir normalize) ; les variantes
# allemandes, italiennes, anglaises et les anciennes communes sont listées explicitement.
_COMMUNES: dict[str, str] = {}


def _add(canton: str, *names: str) -> None:
    for n in names:
        _COMMUNES[normalize(n)] = canton


def normalize(text: str | None) -> str:
    """'Biel/Bienne' → 'biel bienne' ; 'Montagny-près-Yverdon' → 'montagny yverdon' ;
    'Grabs SG' → 'grabs' ; 'Gèneve' → 'geneve' ; 'Le Mont-sur-Lausanne' → 'mont lausanne'."""
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", " ", s)                       # « Zürich (ZH) »
    s = re.sub(r"[-/’'.,]", " ", s)
    s = re.sub(r"\b(pres|sur|sous|les|le|la|l|de|du|des|bei|am|an|im|in|der|di|a)\b", " ", s)
    s = re.sub(r"\b(zh|be|lu|ur|sz|ow|nw|gl|zg|fr|so|bs|bl|sh|ar|ai|sg|gr|ag|tg|ti|vd|vs|ne|ge|ju)\b$", " ", s.strip())
    s = re.sub(r"\bst\b", "saint", s)
    s = re.sub(r"\bsankt\b", "saint", s)
    s = re.sub(r"\bste\b", "sainte", s)
    return re.sub(r"\s+", " ", s).strip()


# --- Bassin nord-vaudois : Jura-Nord vaudois (VD), Broye-Vully (VD) et Broye fribourgeoise limitrophe
BASSIN = set()


def _bassin(canton: str, *names: str) -> None:
    _add(canton, *names)
    BASSIN.update(normalize(n) for n in names)


_bassin("VD", "Yverdon-les-Bains", "Yverdon", "Grandson", "Orbe", "Sainte-Croix", "Vallorbe", "Yvonand",
        "Chavornay", "Baulmes", "Champagne", "Concise", "Montagny-près-Yverdon", "Montagny", "Payerne", "Avenches",
        "Moudon", "Lucens", "Cudrefin", "Échallens", "Echallens", "La Sarraz", "Romainmôtier", "Romainmôtier-Envy",
        "Le Sentier", "Le Chenit", "Le Brassus", "L'Abbaye", "Le Pont", "Le Lieu", "Vaulion", "Bavois",
        "Cheseaux-Noréaz", "Pomy", "Valeyres-sous-Montagny", "Treycovagnes", "Ependes", "Suscévaz", "Cronay",
        "Donneloye", "Bonvillars", "Onnens", "Fiez", "Bullet", "L'Auberson", "Provence", "Corcelles-près-Concise",
        "Orges", "Vugelles-La Mothe", "Essertines-sur-Yverdon", "Vuarrens", "Bioley-Magnoux", "Thierrens",
        "Villars-le-Terroir", "Goumoëns", "Bercher", "Fey", "Oppens", "Penthéréaz", "Pailly", "Rances",
        "Valeyres-sous-Rances", "Ballaigues", "Les Clées", "Lignerolle", "L'Isle", "Montcherand", "Agiez",
        "Arnex-sur-Orbe", "Bofflens", "Croy", "Juriens", "Premier", "Vaugondry", "Mathod", "Belmont-sur-Yverdon",
        "Chamblon", "Champvent", "Démoret", "Molondin", "Rovray", "Villars-Epeney", "Ursins", "Cuarny", "Chêne-Pâquier",
        "Corcelles-sur-Chavornay", "Essert-Pittet", "Giez", "Fontaines-sur-Grandson", "Grandevent", "Novalles",
        "Tévenon", "Mauborget", "Mutrux", "Sergey", "Lucens", "Granges-près-Marnand", "Valbroye", "Villarzel",
        "Trey", "Corcelles-près-Payerne", "Faoug", "Vully-les-Lacs", "Henniez", "Curtilles", "Chavannes-sur-Moudon",
        "Bussy-sur-Moudon", "Dompierre", "Prévonloup", "Lovatens", "Syens", "Vucherens", "Hermenches", "Rossenges",
        "Missy", "Champtauroz", "Treytorrens", "Chevroux", "Grandcour", "Vulliens", "Ropraz", "Peney-le-Jorat",
        "Montanaire", "Thierrens", "Oulens-sous-Échallens", "Assens", "Bottens", "Poliez-Pittet", "Villars-Tiercelin",
        "Froideville", "Saint-Barthélemy", "Bettens", "Sullens", "Penthaz", "Penthalaz", "Daillens", "Éclépens",
        "Orny", "Pompaples", "Ferreyres", "Moiry", "Chevilly", "Cuarnens", "Mont-la-Ville", "Cossonay")
_bassin("FR", "Estavayer-le-Lac", "Estavayer", "Domdidier", "Cugy", "Montagny-les-Monts", "Saint-Aubin",
        "Cheyres", "Bussy", "Font", "Lully", "Cheiry", "Surpierre", "Vuissens", "Murist", "Delley-Portalban",
        "Gletterens", "Belmont-Broye", "Dompierre FR")

# --- Vaud, autres communes
_add("VD", "Lausanne", "Morges", "Nyon", "Vevey", "Montreux", "Renens", "Pully", "Prilly", "Aigle", "Bex", "Gland",
     "Rolle", "Ecublens", "Écublens", "Crissier", "Bussigny", "Epalinges", "Épalinges", "La Tour-de-Peilz",
     "Oron", "Oron-la-Ville", "Villeneuve", "Coppet", "Le Mont-sur-Lausanne", "Chavannes-près-Renens", "Chavannes",
     "Lutry", "Cully", "Bourg-en-Lavaux", "Saint-Prex", "Aubonne", "Château-d'Œx", "Chateau-d'Oex", "Leysin",
     "Ollon", "Villars-sur-Ollon", "Blonay", "Saint-Légier", "Corseaux", "Corsier-sur-Vevey", "Chexbres", "Puidoux",
     "Savigny", "Belmont-sur-Lausanne", "Paudex", "Cheseaux-sur-Lausanne", "Romanel-sur-Lausanne", "Jouxtens-Mézery",
     "Saint-Sulpice", "Préverenges", "Denges", "Lonay", "Tolochenaz", "Etoy", "Étoy", "Allaman", "Perroy",
     "Bursins", "Gilly", "Vinzel", "Luins", "Begnins", "Vich", "Prangins", "Crans-près-Céligny", "Céligny",
     "Founex", "Commugny", "Tannay", "Mies", "Chavannes-de-Bogis", "Chéserex", "Genolier", "Trélex", "Saint-Cergue",
     "Arzier-Le Muids", "Bassins", "Le Vaud", "Duillier", "Eysins", "Signy-Avenex", "Grens", "Borex", "Crassier",
     "Apples", "Bière", "Ballens", "Montricher", "Pampigny", "Sévery", "Cottens", "Colombier VD", "Gollion",
     "Vufflens-la-Ville", "Vullierens", "Villars-Sainte-Croix", "Mex", "Aclens", "Bremblens", "Echandens",
     "Échandens", "Lully VD", "Yens", "Denens", "Villars-sous-Yens", "Lavigny", "Saint-Livres", "Féchy",
     "Bougy-Villars", "Mont-sur-Rolle", "Tartegnin", "Essertines-sur-Rolle", "Dully", "Burtigny", "Longirod",
     "Marchissy", "Gimel", "Saubraz", "Aigle", "Ollon", "Yvorne", "Corbeyrier", "Leysin", "Roche", "Rennaz",
     "Noville", "Chessel", "Lavey-Morcles", "Gryon", "Ormont-Dessus", "Ormont-Dessous", "Les Diablerets",
     "Rossinière", "Rougemont", "Château-d'Oex", "Villeneuve VD", "Veytaux", "Montreux", "Clarens", "Chailly",
     "Mézières VD", "Servion", "Carrouge", "Forel", "Jorat-Mézières", "Montpreveyres", "Le Jorat", "Corcelles-le-Jorat",
     "Vulliens", "Chapelle-sur-Moudon", "Saint-Cierges")

# --- Genève
_add("GE", "Genève", "Geneve", "Genf", "Geneva", "Ginevra", "Gèneve", "Carouge", "Meyrin", "Vernier", "Lancy",
     "Onex", "Thônex", "Thonex", "Chêne-Bougeries", "Chêne-Bourg", "Le Grand-Saconnex", "Grand-Saconnex",
     "Versoix", "Bernex", "Plan-les-Ouates", "Veyrier", "Cologny", "Vandœuvres", "Vandoeuvres", "Pregny-Chambésy",
     "Chambésy", "Bellevue", "Genthod", "Collonge-Bellerive", "Corsier GE", "Anières", "Hermance", "Jussy",
     "Presinge", "Puplinge", "Choulex", "Meinier", "Gy", "Confignon", "Perly-Certoux", "Bardonnex", "Troinex",
     "Satigny", "Russin", "Dardagny", "Aire-la-Ville", "Cartigny", "Avully", "Chancy", "Avusy", "Soral", "Laconnex",
     "Petit-Lancy", "Grand-Lancy", "Châtelaine", "Les Avanchets", "Le Lignon", "Cointrin", "Champel", "Eaux-Vives",
     "Plainpalais", "Servette", "Petit-Saconnex", "Carouge GE")

# --- Fribourg
_add("FR", "Fribourg", "Freiburg", "Friburgo", "Bulle", "Romont", "Morat", "Murten", "Villars-sur-Glâne", "Marly",
     "Châtel-Saint-Denis", "Chatel-Saint-Denis", "Düdingen", "Guin", "Tafers", "Tavel", "Wünnewil-Flamatt", "Flamatt",
     "Kerzers", "Chiètres", "Schmitten", "Plaffeien", "Planfayon", "Riaz", "La Tour-de-Trême", "Broc", "Gruyères",
     "Charmey", "Val-de-Charmey", "Vuadens", "Sâles", "Vaulruz", "Le Pâquier", "Attalens", "Remaufens", "Semsales",
     "Siviriez", "Ursy", "Mézières FR", "Vuisternens-devant-Romont", "Villaz-Saint-Pierre", "Cottens FR",
     "Neyruz", "Matran", "Avry", "Corminbœuf", "Corminboeuf", "Givisiez", "Granges-Paccot", "Belfaux", "Grolley",
     "Courtepin", "Misery-Courtion", "Cressier FR", "Barberêche", "Gurmels", "Cormondes", "Ulmiz", "Bösingen",
     "Heitenried", "Sankt Antoni", "Saint-Antoine", "Alterswil", "Giffers", "Chevrilles", "Rechthalten",
     "Dirlaret", "Sankt Ursen", "Saint-Ours", "Brünisried", "Oberschrot", "Zumholz", "Jaun", "Bellegarde FR",
     "Hauteville", "Corbières", "Botterens", "Pont-la-Ville", "La Roche", "Treyvaux", "Arconciel", "Ependes FR",
     "Le Mouret", "Ferpicloz", "Bonnefontaine", "Praroman", "Senèdes", "Villarsel-sur-Marly", "Pierrafortscha",
     "Rossens", "Farvagny", "Gibloux", "Vuisternens-en-Ogoz", "Rueyres-Saint-Laurent", "Estavayer-le-Gibloux",
     "Villorsonnens", "Prez-vers-Noréaz", "Prez", "Noréaz", "Ponthaux", "Grandsivaz", "Léchelles", "Chandon",
     "Montagny FR", "Cousset", "Mannens", "Torny", "Middes", "Villarimboud", "Billens-Hennens", "Massonnens",
     "Grangettes", "Le Châtelard", "Sorens", "Marsens", "Echarlens", "Morlon", "Gumefens", "Avry-devant-Pont",
     "Pont-en-Ogoz", "Villars-sous-Mont", "Neirivue", "Albeuve", "Lessoc", "Montbovon", "Grandvillard", "Enney",
     "Estavannens", "Haut-Intyamon", "Bas-Intyamon")

# --- Neuchâtel
_add("NE", "Neuchâtel", "Neuchatel", "Neuenburg", "La Chaux-de-Fonds", "Chaux-de-Fonds", "Le Locle", "Colombier",
     "Boudry", "Peseux", "Marin", "Marin-Epagnier", "Val-de-Travers", "Fleurier", "Couvet", "Travers", "Môtiers",
     "Buttes", "Les Verrières", "La Côte-aux-Fées", "Cernier", "Val-de-Ruz", "Fontainemelon", "Les Geneveys-sur-Coffrane",
     "Chézard-Saint-Martin", "Dombresson", "Villiers", "Savagnier", "Fontaines", "Coffrane", "Montmollin", "Valangin",
     "Corcelles-Cormondrèche", "Corcelles NE", "Cormondrèche", "Auvernier", "Bôle", "Rochefort", "Milvignes",
     "Bevaix", "Cortaillod", "Gorgier", "Saint-Aubin-Sauges", "Vaumarcus", "La Grande Béroche", "Hauterive",
     "Saint-Blaise", "La Tène", "Thielle-Wavre", "Cornaux", "Cressier NE", "Le Landeron", "Lignières", "Enges",
     "Les Brenets", "Les Ponts-de-Martel", "La Brévine", "La Sagne", "Brot-Plamboz", "La Chaux-du-Milieu",
     "Le Cerneux-Péquignot", "Les Planchettes", "Serrières")

# --- Valais
_add("VS", "Sion", "Sitten", "Sierre", "Siders", "Martigny", "Monthey", "Brig", "Brigue", "Brig-Glis", "Visp",
     "Viège", "Naters", "Leuk", "Loèche", "Saxon", "Fully", "Conthey", "Savièse", "Saint-Maurice", "Vétroz",
     "Ardon", "Chamoson", "Leytron", "Riddes", "Saillon", "Nendaz", "Vex", "Hérémence", "Evolène", "Saint-Martin VS",
     "Ayent", "Arbaz", "Grimisuat", "Salins", "Veysonnaz", "Bramois", "Uvrier", "Châteauneuf", "Pont-de-la-Morge",
     "Crans-Montana", "Montana", "Lens", "Icogne", "Chermignon", "Randogne", "Mollens", "Miège", "Venthône",
     "Veyras", "Chippis", "Chalais", "Grône", "Saint-Léonard", "Noble-Contrée", "Anniviers", "Vissoie", "Zinal",
     "Grimentz", "Salgesch", "Salquenen", "Varen", "Agarn", "Turtmann", "Tourtemagne", "Gampel", "Steg", "Raron",
     "Rarogne", "Niedergesteln", "Eischoll", "Unterbäch", "Bürchen", "Zermatt", "Täsch", "Randa", "Saas-Fee",
     "Saas-Grund", "Stalden", "Staldenried", "Eisten", "Grächen", "St. Niklaus", "Sankt Niklaus", "Baltschieder",
     "Lalden", "Eggerberg", "Ausserberg", "Ried-Brig", "Termen", "Simplon", "Zwischbergen", "Mörel-Filet", "Bitsch",
     "Riederalp", "Bettmeralp", "Fiesch", "Ernen", "Binn", "Goms", "Münster VS", "Obergoms", "Reckingen",
     "Bagnes", "Verbier", "Le Châble", "Val de Bagnes", "Vollèges", "Sembrancher", "Orsières", "Liddes",
     "Bourg-Saint-Pierre", "Martigny-Combe", "Bovernier", "Salvan", "Finhaut", "Trient", "Vernayaz", "Evionnaz",
     "Dorénaz", "Collonges", "Massongex", "Vérossaz", "Saint-Gingolph", "Port-Valais", "Le Bouveret", "Vouvry",
     "Vionnaz", "Collombey-Muraz", "Collombey", "Muraz", "Troistorrents", "Val-d'Illiez", "Champéry", "Morgins",
     "Charrat", "Isérables", "Chalais", "Sion VS")

# --- Jura
_add("JU", "Delémont", "Delemont", "Delsberg", "Porrentruy", "Pruntrut", "Saignelégier", "Courroux", "Bassecourt",
     "Haute-Sorne", "Courrendlin", "Courtételle", "Develier", "Vicques", "Val Terbi", "Courfaivre", "Glovelier",
     "Boécourt", "Alle", "Bure", "Cornol", "Courgenay", "Fontenais", "Bonfol", "Vendlincourt", "Beurnevésin",
     "Coeuve", "Damphreux", "Lugnez", "Boncourt", "Basse-Allaine", "Courchavon", "Clos du Doubs", "Saint-Ursanne",
     "Haute-Ajoie", "Chevenez", "Grandfontaine", "Fahy", "Bressaucourt", "Le Noirmont", "Les Breuleux",
     "Les Bois", "Muriaux", "Le Bémont", "Lajoux", "Les Genevez", "Montfaucon", "Saint-Brais", "Soubey",
     "Les Enfers", "La Chaux-des-Breuleux", "Rossemaison", "Châtillon JU", "Soyhières", "Mettembert", "Pleigne",
     "Bourrignon", "Movelier", "Ederswiler", "Rebeuvelier", "Mervelier", "Corban", "Courchapoix", "Vermes")

# --- Jura bernois et Bienne (Berne, communes francophones ou bilingues) → Romandie
JURA_BERNOIS = set()
for n in ("Biel/Bienne", "Biel-Bienne", "Bienne", "Biel", "Moutier", "Münster BE", "Saint-Imier", "St-Imier",
          "Tavannes", "Tramelan", "Reconvilier", "La Neuveville", "Courtelary", "Malleray", "Bévilard", "Valbirse",
          "Corgémont", "Sonvilier", "Renan", "Villeret", "Cortébert", "Cormoret", "Péry", "Péry-La Heutte",
          "La Heutte", "Orvin", "Plagne", "Vauffelin", "Sauge", "Romont BE", "Sonceboz", "Sonceboz-Sombeval",
          "Court", "Sorvilier", "Champoz", "Loveresse", "Saules BE", "Saicourt", "Le Fuet", "Bellelay", "Petit-Val",
          "Sornetan", "Souboz", "Grandval", "Crémines", "Corcelles BE", "Eschert", "Belprahon", "Perrefitte",
          "Roches", "Rebévelier", "Nods", "Diesse", "Lamboing", "Prêles", "Plateau de Diesse", "Evilard", "Leubringen",
          "Macolin", "Magglingen", "Bözingen", "Boujean", "Mâche", "Madretsch"):
    _add("BE", n)
    JURA_BERNOIS.add(normalize(n))

# --- Reste de la Suisse (les plus fréquents du vivier + chefs-lieux)
_add("ZH", "Zürich", "Zurich", "Zurigo", "Winterthur", "Uster", "Dübendorf", "Dietikon", "Wetzikon", "Wädenswil",
     "Horgen", "Bülach", "Kloten", "Opfikon", "Schlieren", "Regensdorf", "Adliswil", "Volketswil", "Thalwil",
     "Illnau-Effretikon", "Effretikon", "Wallisellen", "Meilen", "Küsnacht", "Zollikon", "Kilchberg", "Rüti",
     "Stäfa", "Affoltern am Albis", "Richterswil", "Männedorf", "Pfäffikon", "Wald", "Hinwil", "Bassersdorf",
     "Urdorf", "Oberrieden", "Rüschlikon", "Langnau am Albis", "Embrach", "Rümlang", "Niederhasli", "Oetwil",
     "Erlenbach", "Herrliberg", "Uetikon", "Hombrechtikon", "Zumikon", "Maur", "Egg", "Fällanden", "Greifensee",
     "Schwerzenbach", "Wangen-Brüttisellen", "Dielsdorf", "Bachenbülach", "Eglisau", "Andelfingen", "Elgg",
     "Turbenthal", "Bauma", "Fehraltorf", "Russikon", "Weisslingen", "Lindau", "Nürensdorf", "Birmensdorf",
     "Aesch ZH", "Geroldswil", "Weiningen", "Oberengstringen", "Unterengstringen", "Zürich-Oerlikon", "Oerlikon",
     "Altstetten", "Seebach", "Schwamendingen", "Witikon", "Wollishofen", "Höngg", "Affoltern")
_add("BE", "Bern", "Berne", "Berna", "Thun", "Thoune", "Köniz", "Burgdorf", "Berthoud", "Steffisburg", "Ostermundigen",
     "Muri bei Bern", "Langenthal", "Spiez", "Worb", "Interlaken", "Lyss", "Münsingen", "Zollikofen", "Belp",
     "Ittigen", "Bolligen", "Wohlen bei Bern", "Konolfingen", "Oberdiessbach", "Langnau im Emmental", "Herzogenbuchsee",
     "Huttwil", "Sumiswald", "Frutigen", "Meiringen", "Brienz", "Saanen", "Gstaad", "Zweisimmen", "Adelboden",
     "Kandersteg", "Grindelwald", "Wengen", "Lauterbrunnen", "Aarberg", "Büren an der Aare", "Ins", "Erlach",
     "Täuffelen", "Aegerten", "Studen", "Worben", "Kirchberg BE", "Utzenstorf", "Fraubrunnen", "Jegenstorf",
     "Urtenen-Schönbühl", "Moosseedorf", "Münchenbuchsee", "Bremgarten bei Bern", "Kehrsatz", "Wabern", "Bümpliz",
     "Bethlehem", "Riggisberg", "Schwarzenburg", "Wattenwil", "Uetendorf", "Heimberg", "Oberhofen", "Hilterfingen",
     "Sigriswil", "Beatenberg", "Unterseen", "Matten", "Bönigen", "Wilderswil", "Innertkirchen", "Hasliberg")
_add("LU", "Luzern", "Lucerne", "Lucerna", "Emmen", "Emmenbrücke", "Kriens", "Horw", "Ebikon", "Sursee", "Hochdorf",
     "Willisau", "Wolhusen", "Root", "Rothenburg", "Buchrain", "Meggen", "Adligenswil", "Malters", "Ruswil",
     "Schüpfheim", "Entlebuch", "Escholzmatt", "Reiden", "Dagmersellen", "Nebikon", "Zell LU", "Hitzkirch",
     "Beromünster", "Neuenkirch", "Sempach", "Eich", "Schenkon", "Oberkirch", "Knutwil", "Triengen", "Büron",
     "Littau", "Reussbühl", "Inwil", "Eschenbach LU", "Ballwil", "Hohenrain", "Weggis", "Vitznau", "Greppen")
_add("UR", "Altdorf", "Erstfeld", "Schattdorf", "Bürglen", "Flüelen", "Andermatt", "Silenen", "Seedorf", "Attinghausen")
_add("SZ", "Schwyz", "Einsiedeln", "Küssnacht", "Arth", "Goldau", "Freienbach", "Pfäffikon SZ", "Lachen", "Siebnen",
     "Schübelbach", "Wollerau", "Feusisberg", "Altendorf", "Galgenen", "Tuggen", "Reichenburg", "Brunnen", "Ingenbohl",
     "Gersau", "Muotathal", "Steinen", "Sattel", "Rothenthurm", "Wangen SZ", "Vorderthal", "Innerthal")
_add("OW", "Sarnen", "Kerns", "Alpnach", "Sachseln", "Giswil", "Lungern", "Engelberg")
_add("NW", "Stans", "Hergiswil", "Buochs", "Stansstad", "Ennetbürgen", "Beckenried", "Oberdorf NW", "Wolfenschiessen",
     "Dallenwil", "Ennetmoos", "Emmetten")
_add("GL", "Glarus", "Näfels", "Netstal", "Mollis", "Niederurnen", "Glarus Nord", "Glarus Süd", "Linthal", "Schwanden",
     "Ennenda", "Bilten")
_add("ZG", "Zug", "Zoug", "Baar", "Cham", "Steinhausen", "Risch", "Rotkreuz", "Hünenberg", "Unterägeri", "Oberägeri",
     "Aegeri", "Ägeri", "Menzingen", "Neuheim", "Walchwil")
_add("SO", "Solothurn", "Soleure", "Olten", "Grenchen", "Granges SO", "Zuchwil", "Biberist", "Derendingen", "Gerlafingen",
     "Balsthal", "Dornach", "Breitenbach", "Schönenwerd", "Trimbach", "Dulliken", "Däniken", "Bellach", "Langendorf",
     "Lostorf", "Oensingen", "Egerkingen", "Härkingen", "Neuendorf", "Wangen bei Olten", "Hägendorf", "Kappel SO",
     "Gretzenbach", "Wolfwil", "Kestenholz", "Niedergösgen", "Obergösgen", "Erlinsbach", "Lohn-Ammannsegg",
     "Subingen", "Deitingen", "Luterbach", "Riedholz", "Feldbrunnen", "Selzach", "Bettlach", "Lengnau BE")
_add("BS", "Basel", "Bâle", "Basilea", "Riehen", "Bettingen")
_add("BL", "Liestal", "Allschwil", "Reinach BL", "Muttenz", "Pratteln", "Binningen", "Birsfelden", "Münchenstein",
     "Oberwil BL", "Therwil", "Aesch BL", "Arlesheim", "Bottmingen", "Frenkendorf", "Füllinsdorf", "Sissach",
     "Gelterkinden", "Laufen", "Laufen BL", "Ettingen", "Pfeffingen", "Biel-Benken", "Bubendorf", "Lausen", "Itingen",
     "Zunzgen", "Diegten", "Waldenburg", "Oberdorf BL", "Hölstein", "Niederdorf", "Röschenz", "Zwingen", "Grellingen",
     "Duggingen", "Schönenbuch", "Seltisberg", "Augst", "Giebenach", "Arisdorf", "Hersberg", "Ormalingen",
     "Rünenberg", "Böckten", "Thürnen", "Diepflingen")
_add("SH", "Schaffhausen", "Schaffhouse", "Neuhausen am Rheinfall", "Neuhausen", "Thayngen", "Stein am Rhein",
     "Beringen", "Hallau", "Neunkirch", "Wilchingen", "Ramsen", "Löhningen", "Siblingen", "Schleitheim")
_add("AR", "Herisau", "Teufen", "Speicher", "Heiden", "Gais", "Urnäsch", "Trogen", "Bühler", "Waldstatt", "Walzenhausen",
     "Rehetobel", "Lutzenberg", "Grub AR", "Wolfhalden", "Hundwil", "Stein AR", "Schwellbrunn", "Reute", "Wald AR",
     "Schönengrund")
_add("AI", "Appenzell", "Gonten", "Oberegg", "Rüte", "Schwende", "Schlatt-Haslen")
_add("SG", "St. Gallen", "Saint-Gall", "Sankt Gallen", "St Gallen", "San Gallo", "Rapperswil", "Rapperswil-Jona", "Jona",
     "Wil", "Wil SG", "Gossau", "Gossau SG", "Uzwil", "Buchs SG", "Altstätten", "Rorschach", "Flawil", "Wittenbach",
     "Goldach", "Rebstein", "Widnau", "Au SG", "Heerbrugg", "Balgach", "Diepoldsau", "Oberriet", "Rüthi", "Sennwald",
     "Gams", "Grabs", "Sevelen", "Wartau", "Sargans", "Mels", "Vilters-Wangs", "Bad Ragaz", "Pfäfers", "Flums",
     "Walenstadt", "Quarten", "Amden", "Weesen", "Schänis", "Benken", "Kaltbrunn", "Uznach", "Gommiswald",
     "Eschenbach SG", "Schmerikon", "Rieden", "Ebnat-Kappel", "Wattwil", "Lichtensteig", "Nesslau", "Wildhaus-Alt St. Johann",
     "Kirchberg SG", "Bütschwil-Ganterschwil", "Mosnang", "Lütisburg", "Oberbüren", "Niederbüren", "Zuzwil",
     "Oberuzwil", "Jonschwil", "Degersheim", "Andwil", "Waldkirch", "Häggenschwil", "Muolen", "Berg SG", "Tübach",
     "Steinach", "Mörschwil", "Untereggen", "Eggersriet", "Thal", "Rheineck", "Sankt Margrethen", "St. Margrethen",
     "Marbach SG", "Eichberg", "Lüchingen", "Rorschacherberg")
_add("GR", "Chur", "Coire", "Coira", "Davos", "Landquart", "Domat/Ems", "Domat Ems", "Ems", "Igis", "Arosa",
     "St. Moritz", "Sankt Moritz", "Saint-Moritz", "Samedan", "Pontresina", "Zuoz", "Scuol", "Zernez", "Thusis",
     "Ilanz", "Ilanz/Glion", "Flims", "Laax", "Lenzerheide", "Vaz/Obervaz", "Bonaduz", "Rhäzüns", "Tamins",
     "Trin", "Felsberg", "Haldenstein", "Maienfeld", "Malans", "Jenins", "Fläsch", "Zizers", "Trimmis", "Untervaz",
     "Says", "Klosters", "Klosters-Serneus", "Schiers", "Grüsch", "Küblis", "Saas im Prättigau", "Poschiavo",
     "Bregaglia", "Mesocco", "Roveredo", "Grono", "Cama", "Lostallo", "Soazza", "Splügen", "Andeer", "Zillis",
     "Savognin", "Surses", "Bivio", "Tiefencastel", "Albula", "Bergün", "Filisur", "Schmitten GR", "Wiesen",
     "Churwalden", "Malix", "Parpan", "Tschiertschen", "Praden", "Trans", "Cazis", "Sils im Domleschg", "Rothenbrunnen",
     "Paspels", "Rodels", "Pratval", "Fürstenau", "Scharans", "Almens", "Tomils", "Domleschg", "Disentis",
     "Disentis/Mustér", "Sumvitg", "Trun", "Breil/Brigels", "Brigels", "Obersaxen", "Mundaun", "Lumnezia",
     "Vals", "Safiental", "Tujetsch", "Sedrun", "Medel", "Samnaun", "Valsot", "Val Müstair", "Santa Maria",
     "Müstair", "S-chanf", "Madulain", "La Punt", "Bever", "Celerina", "Sils im Engadin", "Silvaplana")
_add("AG", "Aarau", "Aarau AG", "Baden", "Wettingen", "Zofingen", "Brugg", "Rheinfelden", "Lenzburg", "Wohlen AG",
     "Wohlen", "Möhlin", "Spreitenbach", "Oftringen", "Muri AG", "Muri", "Suhr", "Buchs AG", "Aarburg", "Windisch",
     "Reinach AG", "Kaiseraugst", "Obersiggenthal", "Neuenhof", "Rothrist", "Menziken", "Frick", "Laufenburg",
     "Zurzach", "Bad Zurzach", "Bremgarten AG", "Bremgarten", "Villmergen", "Mellingen", "Würenlos", "Killwangen",
     "Fislisbach", "Niederrohrdorf", "Oberrohrdorf", "Gebenstorf", "Turgi", "Untersiggenthal", "Birmenstorf",
     "Würenlingen", "Döttingen", "Klingnau", "Koblenz AG", "Leibstadt", "Full-Reuenthal", "Stein AG", "Münchwilen AG",
     "Eiken", "Kaisten", "Gipf-Oberfrick", "Wittnau", "Wölflinswil", "Zeihen", "Hornussen", "Bözen", "Effingen",
     "Elfingen", "Böztal", "Schinznach", "Schinznach-Bad", "Veltheim AG", "Auenstein", "Rupperswil", "Möriken-Wildegg",
     "Wildegg", "Holderbank AG", "Hunzenschwil", "Schafisheim", "Seon", "Staufen", "Niederlenz", "Othmarsingen",
     "Dintikon", "Hendschiken", "Dottikon", "Hägglingen", "Sarmenstorf", "Bettwil", "Uezwil", "Fahrwangen",
     "Meisterschwanden", "Seengen", "Boniswil", "Hallwil", "Dürrenäsch", "Leutwil", "Birrwil", "Beinwil am See",
     "Burg AG", "Gontenschwil", "Oberkulm", "Unterkulm", "Teufenthal", "Gränichen", "Unterentfelden", "Oberentfelden",
     "Muhen", "Hirschthal", "Holziken", "Schöftland", "Kölliken", "Safenwil", "Walterswil SO", "Strengelbach",
     "Vordemwald", "Brittnau", "Murgenthal", "Wikon", "Kirchleerau", "Moosleerau", "Staffelbach", "Attelwil",
     "Reitnau", "Uerkheim", "Bottenwil", "Zetzwil", "Leimbach AG", "Schmiedrued", "Schlossrued", "Küttigen",
     "Biberstein", "Erlinsbach AG", "Densbüren", "Thalheim AG", "Riniken", "Umiken", "Rüfenach", "Remigen",
     "Mönthal", "Villigen", "Hausen AG", "Mülligen", "Lupfig", "Scherz", "Habsburg", "Birr", "Birrhard", "Bözberg")
_add("TG", "Frauenfeld", "Kreuzlingen", "Arbon", "Amriswil", "Weinfelden", "Romanshorn", "Aadorf", "Bischofszell",
     "Sirnach", "Münchwilen TG", "Egnach", "Steckborn", "Diessenhofen", "Wängi", "Eschlikon", "Tägerwilen",
     "Bottighofen", "Münsterlingen", "Güttingen", "Altnau", "Kesswil", "Uttwil", "Salmsach", "Roggwil TG",
     "Horn TG", "Sommeri", "Hefenhofen", "Erlen", "Bürglen TG", "Sulgen", "Bürglen", "Berg TG", "Birwinken",
     "Märstetten", "Wigoltingen", "Müllheim", "Pfyn", "Hüttlingen", "Felben-Wellhausen", "Gachnang", "Uesslingen-Buch",
     "Warth-Weiningen", "Neunforn", "Herdern", "Hüttwilen", "Mammern", "Eschenz", "Wagenhausen", "Basadingen-Schlattingen",
     "Schlatt TG", "Homburg", "Raperswilen", "Salenstein", "Ermatingen", "Gottlieben", "Lengwil", "Wäldi", "Rickenbach TG",
     "Wilen TG", "Tobel-Tägerschen", "Affeltrangen", "Bettwiesen", "Lommis", "Stettfurt", "Matzingen", "Thundorf",
     "Kradolf-Schönenberg", "Hauptwil-Gottshaus", "Zihlschlacht-Sitterdorf", "Hohentannen", "Amlikon-Bissegg",
     "Bussnang", "Wuppenau", "Schönholzerswilen", "Braunau", "Fischingen", "Bichelsee-Balterswil", "Dozwil", "Kemmental")
_add("TI", "Lugano", "Bellinzona", "Bellinzone", "Locarno", "Mendrisio", "Chiasso", "Giubiasco", "Biasca",
     "Massagno", "Paradiso", "Viganello", "Pregassona", "Sorengo", "Savosa", "Canobbio", "Comano", "Cadempino",
     "Lamone", "Vezia", "Porza", "Cureglia", "Origlio", "Ponte Capriasca", "Capriasca", "Tesserete", "Agno",
     "Bioggio", "Manno", "Gravesano", "Bedano", "Torricella-Taverne", "Taverne", "Monteceneri", "Rivera",
     "Muzzano", "Collina d'Oro", "Montagnola", "Gentilino", "Agra", "Grancia", "Carabbia", "Carona", "Melide",
     "Morcote", "Vico Morcote", "Bissone", "Maroggia", "Melano", "Rovio", "Arogno", "Capolago", "Riva San Vitale",
     "Brusino Arsizio", "Castel San Pietro", "Balerna", "Coldrerio", "Novazzano", "Stabio", "Vacallo", "Morbio Inferiore",
     "Breggia", "Caslano", "Magliaso", "Pura", "Ponte Tresa", "Croglio", "Sessa", "Monteggio", "Tresa", "Neggio",
     "Vernate", "Cademario", "Aranno", "Alto Malcantone", "Curio", "Novaggio", "Bedigliora", "Astano", "Miglieglia",
     "Gordola", "Tenero", "Tenero-Contra", "Minusio", "Muralto", "Orselina", "Brione sopra Minusio", "Losone",
     "Ascona", "Brissago", "Ronco sopra Ascona", "Cugnasco-Gerra", "Lavertezzo", "Verzasca", "Cevio", "Maggia",
     "Avegno Gordevio", "Terre di Pedemonte", "Centovalli", "Onsernone", "Cadenazzo", "Sant'Antonino", "Camorino",
     "Gudo", "Sementina", "Monte Carasso", "Gnosca", "Gorduno", "Claro", "Arbedo-Castione", "Lumino", "Riviera",
     "Cresciano", "Osogna", "Lodrino", "Iragna", "Acquarossa", "Blenio", "Serravalle", "Faido", "Airolo", "Quinto",
     "Prato Leventina", "Dalpe", "Bodio", "Giornico", "Personico", "Pollegio")

# Communes germanophones des cantons bilingues (Haut-Valais, Singine, Lac) : Suisse, pas Romandie.
GERMANOPHONE = {normalize(n) for n in (
    "Brig", "Brigue", "Brig-Glis", "Visp", "Viège", "Naters", "Leuk", "Loèche", "Salgesch", "Salquenen", "Varen",
    "Agarn", "Turtmann", "Tourtemagne", "Gampel", "Steg", "Raron", "Rarogne", "Niedergesteln", "Eischoll",
    "Unterbäch", "Bürchen", "Zermatt", "Täsch", "Randa", "Saas-Fee", "Saas-Grund", "Stalden", "Staldenried",
    "Eisten", "Grächen", "St. Niklaus", "Sankt Niklaus", "Baltschieder", "Lalden", "Eggerberg", "Ausserberg",
    "Ried-Brig", "Termen", "Simplon", "Zwischbergen", "Mörel-Filet", "Bitsch", "Riederalp", "Bettmeralp", "Fiesch",
    "Ernen", "Binn", "Goms", "Münster VS", "Obergoms", "Reckingen",
    "Düdingen", "Guin", "Tafers", "Tavel", "Wünnewil-Flamatt", "Flamatt", "Kerzers", "Chiètres", "Schmitten",
    "Plaffeien", "Planfayon", "Ulmiz", "Bösingen", "Heitenried", "Sankt Antoni", "Saint-Antoine", "Alterswil",
    "Giffers", "Chevrilles", "Rechthalten", "Dirlaret", "Sankt Ursen", "Saint-Ours", "Brünisried", "Oberschrot",
    "Zumholz", "Jaun", "Bellegarde FR", "Gurmels", "Cormondes",
)}

FRONTALIER = {normalize(n) for n in (
    "Pontarlier", "Annecy", "Thonon-les-Bains", "Thonon", "Besançon", "Besancon", "Morteau", "Annemasse",
    "Évian-les-Bains", "Evian", "Évian", "Saint-Julien-en-Genevois", "Gex", "Ferney-Voltaire", "Divonne-les-Bains",
    "Bellegarde-sur-Valserine", "Valserhône", "Saint-Louis", "Cluses", "Bonneville", "Sallanches", "La Roche-sur-Foron",
    "Douvaine", "Bons-en-Chablais", "Publier", "Saint-Claude", "Morez", "Hauts de Bienne", "Champagnole",
    "Les Rousses", "Maîche", "Valdahon", "Ornans", "Baume-les-Dames", "Montbéliard", "Belfort", "Mulhouse",
    "Delle", "Audincourt", "Sochaux", "Valentigney", "Seloncourt", "Hérimoncourt", "Rumilly", "Seynod",
    "Cran-Gevrier", "Meythet", "Reignier", "Gaillard", "Ambilly", "Ville-la-Grand", "Vétraz-Monthoux", "Cranves-Sales",
    "Archamps", "Collonges-sous-Salève", "Saint-Genis-Pouilly", "Prévessin-Moëns", "Thoiry", "Oyonnax", "Nantua",
    "Bellegarde", "Lons-le-Saunier", "Saint-Vit", "Levier", "Frasne", "Mouthe", "Métabief", "Jougne",
    "Les Fourgs", "Vallorbe FR", "Villers-le-Lac", "Le Russey", "Charquemont", "Damprichard", "Pont-de-Roide",
    "Saint-Hippolyte", "Blamont", "Hégenheim", "Huningue", "Sierentz", "Altkirch", "Ferrette", "Passy", "Chamonix",
    "Chamonix-Mont-Blanc", "Megève", "Saint-Gervais-les-Bains", "Faverges", "Ugine", "Albertville", "Bellevaux",
    "Abondance", "Châtel", "Morzine", "Samoëns", "Taninges", "Scionzier", "Marnaz", "Thyez", "Marignier",
    "Ayze", "Contamine-sur-Arve", "Saint-Pierre-en-Faucigny", "Amancy", "Cornier", "Pers-Jussy", "Fillinges",
    "Viuz-en-Sallaz", "Boëge", "Saint-Cergues", "Machilly", "Juvigny", "Veigy-Foncenex", "Chens-sur-Léman",
    "Messery", "Yvoire", "Sciez", "Anthy-sur-Léman", "Margencel", "Allinges", "Marin", "Lugrin", "Meillerie",
    "Saint-Gingolph FR", "Bernex FR", "Vailly", "Reyvroz", "Féternes", "Larringes", "Champanges", "Neuvecelle",
    "Maxilly-sur-Léman", "Vinzier", "Saint-Paul-en-Chablais", "Groisy", "Argonay", "Pringy", "Épagny",
    "Poisy", "Sillingy", "La Balme-de-Sillingy", "Cruseilles", "Allonzier-la-Caille", "Villy-le-Pelloux",
    "Frangy", "Seyssel", "Valleiry", "Vulbens", "Viry", "Beaumont", "Neydens", "Feigères", "Présilly",
    "Bossey", "Étrembières", "Monnetier-Mornex", "Arthaz-Pont-Notre-Dame", "Nangy", "Scientrier", "Arenthon",
    "Contamine", "Ayse", "Vougy", "Magland", "Mieussy", "Saint-Jeoire", "Onnion", "Mégevette", "Habère-Poche",
    "Lullin", "Le Lyaud", "Armoy", "Orcier", "Draillant", "Perrignier", "Cervens", "Brenthonne", "Fessy",
    "Lully FR", "Ballaison", "Loisin", "Massongy", "Excenevex", "Nernier", "Anthy", "Sciez-sur-Léman", "Bellerive",
)}

ROMAND_CLUBS = ("yverdon", "lausanne", "servette", "sion", "xamax", "neuchâtel", "neuchatel", "fribourg",
                "étoile carouge", "etoile carouge", "stade nyonnais", "nyon", "bulle", "la chaux-de-fonds",
                "stade lausanne", "ouchy", "meyrin", "bavois", "vevey", "echallens", "échallens", "grandson",
                "delémont", "delemont", "monthey", "martigny", "bienne", "biel", "team vaud", "concordia lausanne",
                "le mont", "chênois", "chenois", "lancy", "versoix", "portalban", "estavayer", "payerne", "moudon",
                "sainte-croix", "orbe", "vallorbe", "yvonand", "bex", "aigle", "montreux", "renens", "prilly",
                "pully", "morges", "gland", "rolle", "cossonay", "la sarraz", "colombier", "boudry", "le locle",
                "sierre", "savièse", "saviese", "fully", "saxon", "conthey", "porrentruy", "team jura", "team fribourg",
                "team valais", "team genève", "team geneve", "team neuchâtel", "team neuchatel", "team be-jura",
                "team bejune")


def canton(city: str | None, country: str | None = None) -> str | None:
    """Canton d'une commune suisse ; None si la commune est inconnue ou si le pays n'est pas la Suisse."""
    c = (country or "").strip().lower()
    if c and c not in SWISS_COUNTRY:
        return None
    key = normalize(city)
    if not key:
        return None
    if key in _COMMUNES:
        return _COMMUNES[key]
    # « Lausanne-Vennes », « Zürich Oerlikon » : on tente la première commune citée.
    for part in re.split(r"\s+", key):
        if part in _COMMUNES and len(part) > 3:
            return _COMMUNES[part]
    return None


def zone_of_birth(city: str | None, country: str | None = None, citizenships=()) -> str:
    """Zone d'ancrage d'après le seul lieu de naissance (et à défaut le pays / la nationalité)."""
    c = (country or "").strip().lower()
    key = normalize(city)
    ct = canton(city, country)
    if ct:
        if key in BASSIN:
            return "Bassin nord-vaudois"
        if ct == "VD":
            return "Vaud"
        if (ct in ROMAND_CANTONS and key not in GERMANOPHONE) or key in JURA_BERNOIS:
            return "Romandie"
        return "Suisse"
    if c in FRENCH_COUNTRY and key in FRONTALIER:
        return "Frontalier"
    if not c and key in FRONTALIER and key not in _COMMUNES:
        return "Frontalier"
    if c in SWISS_COUNTRY or any((n or "").lower() in SWISS_COUNTRY for n in citizenships):
        return "Suisse"
    return "International"


def _club_match(needle: str, haystack: str) -> bool:
    """Nom de club entier (« rolle » ne matche pas « Buxerolles », « biel » pas « Bielefeld »)."""
    return re.search(r"(?<![a-zà-ÿ])" + re.escape(needle) + r"(?![a-zà-ÿ])", haystack) is not None


def zone_of_youth(youth_clubs=()) -> str | None:
    """Zone d'après le club formateur : Yverdon → bassin ; club romand → Romandie ; sinon None."""
    youth = " ".join(youth_clubs or ()).lower()
    if "yverdon" in youth:
        return "Bassin nord-vaudois"
    if any(_club_match(c, youth) for c in ROMAND_CLUBS):
        return "Romandie"
    return None


def zone(city: str | None, country: str | None = None, citizenships=(), youth_clubs=()) -> tuple[str, float]:
    """(zone, ancrage) : la meilleure des deux lectures, lieu de naissance et club formateur."""
    z = zone_of_birth(city, country, citizenships)
    y = zone_of_youth(youth_clubs)
    if y and ZONE_SCORE[y] > ZONE_SCORE[z]:
        z = y
    return z, ZONE_SCORE[z]


def unrecognized(players: list[dict], n: int = 20) -> list[tuple[str, int]]:
    """Lieux de naissance absents des tables, les n plus fréquents : joueurs nés en Suisse ou
    sans pays de naissance renseigné (un lieu français non frontalier est attendu « International »
    et n'est pas listé)."""
    cnt: Counter = Counter()
    for p in players:
        city, country = p.get("birth_city"), (p.get("birth_country") or "").lower()
        if not city or (country and country not in SWISS_COUNTRY):
            continue
        if canton(city, None) is None and normalize(city) not in FRONTALIER:
            cnt[city] += 1
    return cnt.most_common(n)
