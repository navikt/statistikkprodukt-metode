create table obt_befolkning (
    periode varchar2(7 char) not null,
    alder number(3) not null,
    kjonn varchar2(10) not null,
    antall number(10) not null
);

comment on table obt_befolkning is
    '#NAVN Befolkning #INNHOLD Befolkning fordelt etter periode, alder og kjønn.';
comment on column obt_befolkning.periode is
    '#NAVN Periode #INNHOLD Månedsperiode på formatet YYYYM01–YYYYM12.';
comment on column obt_befolkning.alder is
    '#NAVN Alder #INNHOLD Alder i ettårige grupper.';
comment on column obt_befolkning.kjonn is
    '#NAVN Kjønn #INNHOLD Kjønnskategori.';
comment on column obt_befolkning.antall is
    '#NAVN Antall personer #INNHOLD Antall personer i gruppen.';

insert into obt_befolkning (periode, alder, kjonn, antall) values ('2025M01', 0, 'Kvinner', 26000);
insert into obt_befolkning (periode, alder, kjonn, antall) values ('2025M01', 0, 'Menn', 28000);
insert into obt_befolkning (periode, alder, kjonn, antall) values ('2025M01', 1, 'Kvinner', 27000);
insert into obt_befolkning (periode, alder, kjonn, antall) values ('2025M01', 1, 'Menn', 29000);
insert into obt_befolkning (periode, alder, kjonn, antall) values ('2025M01', 2, 'Kvinner', 27000);
insert into obt_befolkning (periode, alder, kjonn, antall) values ('2025M01', 2, 'Menn', 30000);
insert into obt_befolkning (periode, alder, kjonn, antall) values ('2025M02', 0, 'Kvinner', 26100);
insert into obt_befolkning (periode, alder, kjonn, antall) values ('2025M02', 0, 'Menn', 28000);
insert into obt_befolkning (periode, alder, kjonn, antall) values ('2025M02', 1, 'Kvinner', 27100);
insert into obt_befolkning (periode, alder, kjonn, antall) values ('2025M02', 1, 'Menn', 29000);
insert into obt_befolkning (periode, alder, kjonn, antall) values ('2025M02', 2, 'Kvinner', 27100);
insert into obt_befolkning (periode, alder, kjonn, antall) values ('2025M02', 2, 'Menn', 30000);

commit;
