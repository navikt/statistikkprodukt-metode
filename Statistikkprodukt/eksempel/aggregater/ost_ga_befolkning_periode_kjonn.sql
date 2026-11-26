select periode, kjonn, sum(antall) as antall
from obt_befolkning
group by periode, kjonn
