# statistikkprodukt-metode

Dette repoet inneholder interne arbeidsverktøy for å maskere/prikke små tall i statistikkprodukter og integrere med statistikkbanken.

Komponenter:
- R-pakke for gauss suppression: [README R-pakke](NavDataprodukt/README.md). Målgruppen er statistikkere i Navs statistikkseksjon.
- CLI-verktøy for å kjøre prikkemetodikken: [README CLI verktøy](Statistikkprodukt/README.md). Målgruppen er data engineers.

## Hensikt og metode

Samlede statistiske metoder for interne arbeidsverktøy for bruk i Navs statistikkseksjon, for å sikre felles arbeidsmetodikk og kvalitetssikrede løsninger.
For statistiske metoder som prikking, bygger arbeidsverktøyene til Nav på allerede eksisterende R-funksjon fra SSB, GaussSuppressionFromData() i biblioteket GaussSuppression.
https://cran.r-project.org/web/packages/GaussSuppression/index.html
https://cran.r-project.org/web/packages/GaussSuppression/refman/GaussSuppression.html#GaussSuppressionFromData

Interne arbeidsmetodikker finnes her:
- [Prikking](https://confluence.adeo.no/spaces/navdvh/pages/852298026/Prikking+-+GaussSuppression)
- [Statistikkprodukt](https://confluence.adeo.no/spaces/navdvh/pages/860129264/Prosedyre+-+Smale+aggregater)

# Bruk av KI
KI er blitt benyttet for å generere deler av koden.

# Henvendelser
Spørsmål knyttet til koden eller repositoryen kan stilles som issues her på GitHub.

## For Nav ansatte
Interne henvendelser kan sendes via Slack i kanalen #dvh-miljo.

# Lisens
MIT

