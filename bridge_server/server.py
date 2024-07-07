import json, os
import asyncio
import aiohttp
import logging
from aiohttp import web
from .urls import setup_routes
from .assistant import (queue_prompt,
                    get_history,
                    delete_history,
                    get_queue_state,
                    get_parsed_input_nodes,
                    parse_workflow_prompt,
                    parse_outputs,
                    save_binary_file,
                    AsyncJsonWrapper)

class BridgeServer():
    
    def __init__(self, 
                 loop, 
                 state_fn:str,
                 comfyui_dir:str,
                 wf_dir:str, 
                 server_address:list, 
                 limit_timeout_count:int, 
                 timeout_interval:int,
                 upload_max_size:int=1024**2*100
                 ) -> None:
        self.loop = loop
        self.lock = asyncio.Lock()

        self.sockets_res = {}
        self.sockets_req = {}
        self.sid_server_map = {}
        self.wf_info = {}
        self.ws_connection_status = {}
        self.execution_info = {}
        self.history_life = {}

        self.comfyui_dir = comfyui_dir
        self.wf_dir = wf_dir
        self.server_address = server_address
        self.limit_timeout_count = limit_timeout_count
        self.timeout_interval = timeout_interval
        self.upload_max_size = upload_max_size

        self.state_obj = AsyncJsonWrapper(state_fn)

    async def init_app(self):
        app = web.Application(client_max_size=self.upload_max_size)
        setup_routes(app, self)

        await self.state_obj.load()
        return app
    
    async def update_dict(self, dict_name, key, value):
        async with self.lock:
            getattr(self, dict_name)[key] = value

    async def get_from_dict(self, dict_name, key):
        async with self.lock:
            return getattr(self, dict_name).get(key, None)
    
    async def pop_from_dict(self, dict_name, key):
        async with self.lock:
            return getattr(self, dict_name).pop(key, None)
    
    async def run_from_dict(self, dict_name, key, function, *args, **kwargs):
        async with self.lock:
            dict_obj = getattr(self, dict_name)
            value = dict_obj.get(key, None)
        return await getattr(value, function)(*args, **kwargs)
        
    async def manage_status_and_sending_message(self, sid, message):
        if await self.get_from_dict("sockets_res", sid) is not None:
            try:
                await self.run_from_dict("sockets_res", sid, "send_json", message)
                await self.update_dict("ws_connection_status", sid, message.get("status", "error"))
                logging.debug(f"[WS RES] SEND OK / {message} / {sid}")

            except Exception as err:
                await self.update_dict("ws_connection_status", sid, "error")
                logging.debug(f"[WS RES] SEND FAILED / {err} / {message} / {sid}")
        else:
            await self.update_dict("ws_connection_status", sid, message.get("status", "error"))
        await self.update_dict("execution_info", sid, message)
    
    async def track_progress(self, sid):
        total_progress = 0
        cur_progress = 0

        logging.info(f"[WS REQ] TRACING START / {sid}")
        while True:
            out = await self.run_from_dict("sockets_req", sid, "receive")
            out = out.data

            if await self.get_from_dict("ws_connection_status", sid) == "closed" or await self.get_from_dict("ws_connection_status", sid) == "error":
                break

            if isinstance(out, str):
                message = json.loads(out)
                
                if message['type'] == 'execution_start':
                    logging.info(f"[WS REQ] EXECUTION START / {sid}")

                    wf_info = await self.get_from_dict("wf_info", sid)
                    inputs_infos = [cur.get("inputs", None) for cur in wf_info.values()]
                    steps_list = [value for inputs_info in inputs_infos
                        for key, value in inputs_info.items()
                        if "steps" in key]
                    total_progress += (len(wf_info) + sum(steps_list))
                    progress_message = {
                        'status': 'progress',
                        'details': f'{cur_progress/total_progress*100:.2f}%'
                    }
                    await self.manage_status_and_sending_message(sid, progress_message)
                
                if message['type'] in ('progress', 'executing'):
                    data = message['data']

                    if data['node'] is None and data['prompt_id'] == sid:
                        progress_message = {
                            'status': 'closed',
                            'details': 'Execution is done'
                        }
                    else:
                        cur_progress += 1
                        progress_message = {
                            'status': 'progress',
                            'details': f'{cur_progress/total_progress*100:.2f}%'
                        }
                    await self.manage_status_and_sending_message(sid, progress_message)
                    
                if message['type'] == 'execution_cached':
                    logging.debug(f"[WS REQ] EXECUTION DONE / {sid}")
                    
                    cached_nodes = message['data']['nodes']
                    cur_progress += len(cached_nodes)
                    progress_message = {
                        'status': 'progress',
                        'details': f'{cur_progress/total_progress*100:.2f}%'
                    }
                    await self.manage_status_and_sending_message(sid, progress_message)

            else:
                continue
        logging.info(f"[WS REQ] TRACING DONE / {sid}")

    async def websocket_connection(self, request, mode):
        sid = request.rel_url.query.get('clientId', '')
        logging.info(f"[WS RES] RECEIVED / {sid}")

        session = None
        try:
            session = await self._ws_req_connection(sid)
            if mode == "PROXY":
                await self._ws_res_connection(request, sid)
            elif mode == "REST":
                pass
            else:
                raise ValueError(f"websocket connection mode must be 'PROXY' or 'REST' but got '{mode}'")
            await self.manage_status_and_sending_message(sid, {"status":"connected", "details":"web socket connected"})

            task = asyncio.create_task(self.track_progress(sid))
            if mode == "PROXY":
                timeout_count = 0
                while True:
                    if await self.get_from_dict("ws_connection_status", sid) == "closed" or await self.get_from_dict("ws_connection_status", sid) == "error":
                        break
                    else:
                        await self.run_from_dict("sockets_res", sid, "send_json", {"status":"listening", "details":"server is listening"})
                        if timeout_count >= self.limit_timeout_count:
                            raise TimeoutError(f"timeout count: {timeout_count}")
                        timeout_count += 1

                    await asyncio.sleep(self.timeout_interval)
            await task

        except aiohttp.ServerDisconnectedError as e:
            logging.warning(f"[WS RES] SERVER DISCONNECTED ERROR / {sid}")
            await self.manage_status_and_sending_message(sid, {"status":"error", "details":"server disconnected"})
        except TimeoutError as e:
            logging.warning(f"[WS RES] TIMEOUT ERROR / {sid}")
            await self.manage_status_and_sending_message(sid, {"status":"error", "details":f"time out error: exceed {self.limit_timeout_count * self.timeout_interval}s"})
        except aiohttp.ServerConnectionError as e:
            logging.error(f"[WS REQ] SERVER CONNECTION ERROR / {sid}")
            await self.manage_status_and_sending_message(sid, {"status":"error", "details":"server connection error"})
        except Exception as e:
            logging.error(f"[WS] UNKNOWN ERROR / {sid}")
            await self.manage_status_and_sending_message(sid, {"status":"error", "details":str(e)})
        finally:
            logging.info(f"[WS] CLOSING / {sid}")
            await self.manage_status_and_sending_message(sid, {"status":"closed", "details":"connection will be closed"})

            if await self.get_from_dict("sockets_req", sid) is not None:
                await self.run_from_dict("sockets_req", sid, "close")
                await self.pop_from_dict("sockets_req", sid) 
            if await self.get_from_dict("sockets_res", sid) is not None:
                await self.run_from_dict("sockets_res", sid, "close")
                await self.pop_from_dict("sockets_res", sid) 
            if session is not None:
                await session.close()
            
            await self.pop_from_dict("ws_connection_status", sid) 
            await self.pop_from_dict("wf_info", sid)

        return web.Response(text="Dummy response")
    
    async def _ws_res_connection(self, request, sid):
        ws_res = web.WebSocketResponse()
        await self.update_dict("sockets_res", sid, ws_res)
        await self.run_from_dict("sockets_res", sid, "prepare", request)
        logging.info(f"[WS RES] HANDSHAKE / {sid}")

    async def _ws_req_connection(self, sid):
        server_address = await self.get_not_busy_server_address()
        await self.update_dict("sid_server_map", sid, server_address)
        logging.debug(f"[WS REQ] server allocated to {server_address} / {sid}")

        session = aiohttp.ClientSession()
        try: 
            ws_req = await session.ws_connect(f"ws://{server_address}/ws?clientId={sid}")
            logging.info(f"[WS REQ] HANDSHAKE / {sid}")
        except Exception as e:
            raise aiohttp.ServerConnectionError
        finally:
            await self.update_dict("sockets_req", sid, ws_req)
            return session

    async def get_not_busy_server_address(self):
        queue_lenghs = []
        for server_address in self.server_address:
            try:
                queue_state = get_queue_state(server_address)
            except Exception as e:
                logging.debug(f"[NO SIGNAL] {server_address} / {e}")

            queue_length = sum([len(cur) for cur in queue_state.values()])
            queue_lenghs.append(queue_length)
        
        target_server_address = self.server_address[queue_lenghs.index(min(queue_lenghs))]
        return target_server_address
    
    async def generate_based_workflow(self, request):
        data = await request.json()
        sid = request.rel_url.query.get('clientId', '')

        workflow = data.pop("workflow", None)
        kwargs = data

        prompt = parse_workflow_prompt(os.path.join(self.wf_dir, workflow), **kwargs)
        await self.update_dict("wf_info", sid, prompt)

        if await self.get_from_dict("sockets_res", sid) is None:
            asyncio.create_task(self.websocket_connection(request, mode="REST"))
        
        prompt = queue_prompt(prompt, sid, await self.get_from_dict("sid_server_map", sid))

        self.state_obj.generation_count += 1
        await self.state_obj.update()

        return web.Response(status=200)
    
    async def upload_image(self, request):
        reader = await request.multipart()

        logging.info(f"[POST] '{request.path}'")

        fns={}
        async for part in reader:

            try:
                file_name = part.headers.get('Content-Disposition', '').split('filename=')[1].strip('"')
                file_name = os.path.basename(file_name)
                file_data = await part.read()
            
                fn = save_binary_file(file_data, file_name, directory=os.path.join(self.comfyui_dir, "input"))
                fns[part.headers.get("ori_file_id", None)] = fn
                logging.debug(f"[POST] '{request.path}' / {fn} saved")
        
            except Exception as e:
                logging.error(f"[POST] '{request.path}' / {file_name} can't save / {str(e)}")

                return web.Response(
                    status=400,
                    body=json.dumps({"detail":f"{file_name} can't save"}),
                    headers={"Content-Type": "application/json"}
                )

        return web.Response(
            status=200,
            body=json.dumps(fns),
            headers={"Content-Type": "application/json"}
        )
        
    async def get_history(self, request):
        sid = request.rel_url.query.get('clientId', '')
        
        server_address = await self.get_from_dict("sid_server_map", sid)
        if server_address is None:
            return web.Response(
                status=204,
                body=json.dumps({"detail":f"The client ID has not been submitted to the server before. It is not recognized. / {sid}"}),
                headers={"Content-Type": "application/json"}
            )
        
        history = get_history(sid, server_address)
        history = history.get(sid, None)
        logging.debug(f"[GET] '{request.path}' / GET HISTORY / {sid}")

        if history is not None:
            output = history["outputs"],
            if isinstance(output, tuple):
                output = (output)
            
            writer = aiohttp.MultipartWriter("form-data")
            
            file_paths, mime_types, file_contents = parse_outputs(output[0], root_dir=self.comfyui_dir)
            for file_path, mime_type, file_content in zip(file_paths, mime_types, file_contents):

                headers = {'Content-Type': mime_type, 'Content-Disposition': f'attachment; filename="{file_path.split("/")[-1]}"'}
                writer.append(file_content, headers)

            headers = {
                'Content-Type': writer.content_type,
            }
            
            await self.pop_from_dict("sid_server_map", sid)
            await self.pop_from_dict("execution_info", sid) 
            delete_history(sid, server_address)
            logging.debug(f"[GET] '{request.path}' / DELETE HISTORY / {sid}")

            return web.Response(
                status=200,
                body=writer,
                headers=headers
            )
        else:
            return web.Response(
                status=204,
                body=json.dumps({"detail":f"No contents with that client id / {sid}"}),
                headers={"Content-Type": "application/json"}
            )
    
    async def workflow_info(self, request):
        data = await request.json()
        workflow = data.pop("workflow", None)
        node_info = get_parsed_input_nodes(os.path.join(self.wf_dir, workflow))
        return web.Response(status=200, body=json.dumps(node_info), content_type="application/json")
    
    async def get_workflow_list(self, request):
        wf_list = os.listdir("./workflows")
        return web.Response(status=200, body=json.dumps(wf_list), content_type="application/json")

    async def get_generation_count(self, request):
        generation_count = self.state_obj.generation_count
        return web.Response(status=200, body=json.dumps(generation_count), content_type="application/json")
    
    async def get_execution_info(self, request):
        sid = request.rel_url.query.get('clientId', '')
        execution_info = await self.get_from_dict("execution_info", sid)
        return web.Response(status=200, body=json.dumps(execution_info), content_type="application/json")

    async def main_page(self, request):
        return web.Response(text="Hello, this is ComfyUI Bridge Server! (made by middlek)")
