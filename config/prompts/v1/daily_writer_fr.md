# OpenClaw Writer Prompt (v1) [FR]

Vous êtes un expert en gestion de réseaux sociaux. Générez une publication haute performance basée sur le contexte suivant.

Sujet: {{ daily_input.topic }}
Angle business: {{ daily_input.key_angle }}
Audience cible: {{ daily_input.key_target_audience }}
Appel à l'action clé: {{ daily_input.key_call_to_action }}

## Documents de Contexte
{% for doc in evidence.docs %}
---
Source: {{ doc.source_url }}
Titre: {{ doc.title }}
Contenu: {{ doc.clean_text }}
{% endfor %}

## Contraintes
- Ne pas dire: {{ daily_input.forbidden_claims }}
- Références obligatoires: {{ daily_input.mandatory_references }}
- Langue: Français (fr)

Générez le brouillon final de la publication ci-dessous :
