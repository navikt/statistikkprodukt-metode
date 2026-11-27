create table obt_fa_kontantstotte_kvartal (
    aar_kvartal_besk_kort varchar2(10 char) not null,
    kjonn_flertall_besk varchar2(10 char) not null,
    ti_aar_gruppe_besk varchar2(10 char) not null,
    kontantstotte_antall_mottakere number(10) not null
);

comment on table obt_fa_kontantstotte_kvartal is
    '#NAVN OBT_FA_KONTANTSTOTTE_KVARTAL #INNHOLD OBT tabell';
comment on column obt_fa_kontantstotte_kvartal.aar_kvartal_besk_kort is
    '#NAVN AAR_KVARTAL_BESK_KORT #INNHOLD År kvartal på formen: 2007k01';
comment on column obt_fa_kontantstotte_kvartal.kjonn_flertall_besk is
    '#NAVN KJONN_FLERTALL_BESK #INNHOLD Kjønn i flertall (Kvinner, Menn)';
comment on column obt_fa_kontantstotte_kvartal.ti_aar_gruppe_besk is
    '#NAVN TI_AAR_GRUPPE_BESK #INNHOLD Aldersgruppering i 10 års intervaller til 90 år på formen: 0-9 år 10-19 år 20-29 år .. 80-89 år 90 år og over';
comment on column obt_fa_kontantstotte_kvartal.kontantstotte_antall_mottakere is
    '#NAVN KONTANTSTOTTE_ANTALL_MOTTAKERE #INNHOLD Antall mottakere av kontantstøtte.';

insert into obt_fa_kontantstotte_kvartal (aar_kvartal_besk_kort, kjonn_flertall_besk, ti_aar_gruppe_besk, kontantstotte_antall_mottakere)
values ('2026K1', 'Kvinner', '20-29 år', 2);

insert into obt_fa_kontantstotte_kvartal (aar_kvartal_besk_kort, kjonn_flertall_besk, ti_aar_gruppe_besk, kontantstotte_antall_mottakere)
values ('2026K1', 'Menn', '20-29 år', 100);

insert into obt_fa_kontantstotte_kvartal (aar_kvartal_besk_kort, kjonn_flertall_besk, ti_aar_gruppe_besk, kontantstotte_antall_mottakere)
values ('2026K2', 'Kvinner', '20-29 år', 110);

insert into obt_fa_kontantstotte_kvartal (aar_kvartal_besk_kort, kjonn_flertall_besk, ti_aar_gruppe_besk, kontantstotte_antall_mottakere)
values ('2026K2', 'Menn', '20-29 år', 120);

commit;
