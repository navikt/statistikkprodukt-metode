# statistikkprodukt-metode

Dette repoet inneholder en R-pakke til bruk for Navs statistikere for å vurdere prikkemetode for sine statistikker, i tillegg til et python CLI-verktøy som benytter en av funksjonene, slik at data engineers får integrert prikkemetodikken med data fra datavarehuset. 

## Hensikt og metode

Samlede statistiske metoder for interne arbeidsverktøy for bruk i Navs statistikkseksjon, for å sikre felles arbeidsmetodikk og kvalitetssikrede løsninger.
For statistiske metoder som prikking, bygger arbeidsverktøyene til Nav på allerede eksisterende R-funksjon fra SSB, GaussSuppressionFromData() i biblioteket GaussSuppression.
https://cran.r-project.org/web/packages/GaussSuppression/index.html
https://cran.r-project.org/web/packages/GaussSuppression/refman/GaussSuppression.html#GaussSuppressionFromData

Programmeringsspråk:
- R
- Python

Interne arbeidsmetodikker finnes her:
Prikking:
https://confluence.adeo.no/spaces/navdvh/pages/852298026/Prikking+-+GaussSuppression

Statistikkprodukt:
https://confluence.adeo.no/spaces/navdvh/pages/860129264/Prosedyre+-+Smale+aggregater

## Struktur
- NavDataprodukt inneholder R-pakken. [Readme R-pakke](NavDataprodukt/README.md)
- Statistikkprodukt inneholder python-koden. [Readme python verktøy](Statistikkprodukt/README.md)



KI er blitt benyttet for å generere deler av koden.

