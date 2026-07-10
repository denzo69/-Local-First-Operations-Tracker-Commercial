# Local-First Operations Tracker

## Yksityiskohtainen projektisuunnitelma v1

## Projektin tavoite

Rakennetaan paikallisesti toimiva selainpohjainen toiminnanohjausjärjestelmä pienyrityksille. Järjestelmä toimii yhdellä Windows-palvelinkoneella, ja muut laitteet käyttävät sitä selaimella. Internet-yhteyttä ei tarvita normaalikäytössä.

## Pääominaisuudet

- Asiakasrekisteri
- Työtilaukset
- Muokattavat työvaiheet
- Dashboard / aamunäkymä
- Muistutukset ennen noutoa
- Tuotteet ja hinnasto
- Työrivit
- Tulostettavat kuitit
- Juokseva kuittinumero
- Muokattavat asetukset
- Varasto ja varaston arvo
- Varmuuskopiot

## Käyttöliittymä

Selainpohjainen käyttöliittymä, joka toimii Windowsissa, Macissa, tabletilla ja puhelimessa.

Käyttöliittymä on responsiivinen. Dashboard näyttää myöhässä olevat, tänään ja huomenna noudettavat sekä valmiit työt.

## Asennusmallit

1. Local Only - toimii vain yrityksen sisäverkossa.
2. VPN/Tailscale - turvallinen etäkäyttö.
3. Cloud - AWS tai muu palvelin.
4. Offline Mode - voidaan eristää kokonaan internetistä.

## Tekniikat

- Backend: FastAPI
- Tietokanta: SQLite, myöhemmin PostgreSQL
- ORM: SQLAlchemy
- Frontend: Jinja2 + HTMX + Bootstrap
- Testaus: Pytest
- Tulostus: HTML + Print CSS

## Tietokantamallit

- customers
- jobs
- job_statuses
- products
- job_items
- receipts
- settings
- audit_log

Myöhemmin:

- inventory
- users
- attachments

## Ensimmäinen MVP

1. Asiakkaat
2. Työt
3. Noutopäivät
4. Dashboard
5. Tilapäivitykset
6. Kuittinumerointi
7. Tulostettava vastaanottokuitti
8. Tuotteet ja hinnat
9. Työrivit
10. Yhteissumman laskenta

## Jatkokehitys

- Varasto
- Raportit
- PDF- ja Excel-vienti
- Käyttäjäroolit
- Lokit
- Docker
- Pilviasennus
- Moniyritystuki

## Portfolion arvo

Projekti osoittaa osaamista ohjelmistoarkkitehtuurista, FastAPI:sta, tietokannoista, käyttöliittymistä, testauksesta, tulostuksesta, offline-ajattelusta ja laajennettavasta järjestelmäsuunnittelusta.
