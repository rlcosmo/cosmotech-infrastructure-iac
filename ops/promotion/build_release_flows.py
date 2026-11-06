"""Reuse the reviewed stage runner and move only the operational execution views."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


def build(definition: dict) -> tuple[dict,dict]:
    main={k:definition[k] for k in ['inputs','tasks','errors','concurrency']}
    # API serializes dynamic maps/lists as JSON strings; source YAML uses native values.
    for task in main['tasks']:
        for name in ['env','commands']:
            if isinstance(task.get(name),str):task[name]=json.loads(task[name])
        for name,code in task.get('inputFiles',{}).items():
            if '\n' not in code and '\\n' in code:task['inputFiles'][name]=code.replace('\\n','\n')
    main.update(id='release_pipeline',namespace='cosmotech.promotion',description='Uma execução principal por release. CI real correlacionada, GLPI e gate; Canary e12h acompanhados pelo scheduler persistente e portal.',labels={'execution_type':'release'})
    main['tasks'].append({'id':'acompanhamento_operacional','type':'io.kestra.plugin.core.log.Log','message':'Controle inicial concluído. Canary, validação, observação12h e encerramento continuam no portal operacional e scheduler persistente; SUCCESS desta execução não encerra a GMUD.'})
    main['errors'][0]['message']='Promoção bloqueada. Consultar a etapa e o motivo no portal. Nenhuma aprovação é criada automaticamente em nome de um usuário.'
    sample=json.loads(json.dumps(next(t for t in main['tasks'] if t['id']=='verified_evidence')))
    sample['id']='reconcile';sample['env']['STAGE']='tick'
    scheduler={'id':'promotion_reconcile','namespace':'cosmotech.operations','description':'Processo técnico persistente. Aprovações, observação e rollback correlacionados às releases; não é uma execução de release.',
               'labels':{'execution_type':'technical','component':'promotion_reconcile'},'tasks':[sample],
               'triggers':[{'id':'every_minute','type':'io.kestra.plugin.core.trigger.Schedule','cron':'*/1 * * * *','timezone':'UTC'}],
               'concurrency':{'limit':1,'behavior':'FAIL'}}
    return main,scheduler


def main():
    parser=argparse.ArgumentParser();parser.add_argument('definition');parser.add_argument('output');args=parser.parse_args()
    source=json.loads(Path(args.definition).read_text());release,scheduler=build(source)
    folder=Path(args.output);folder.mkdir(exist_ok=True,parents=True)
    for name,flow in [('release_pipeline',release),('promotion_reconcile',scheduler)]:
        (folder/(name+'.yaml')).write_text(yaml.safe_dump(flow,sort_keys=False,allow_unicode=True))


if __name__=='__main__':main()
