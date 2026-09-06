#include <stdexcept>
#include <algorithm>
#include <inttypes.h>
#include <vector>
#include "Engine.h"
#include "../utils/Logical_Address_Partitioning_Unit.h"

namespace MQSimEngine
{
	Engine* Engine::_instance = NULL;

	Engine* Engine::Instance() {
		if (_instance == 0) {
			_instance = new Engine;
			_instance->_sim_time = 0;
			_instance->stop = false;
			_instance->event_stats_enabled = false;
			_instance->event_trace_first = 0;
			_instance->event_trace_printed = 0;
			_instance->registered_event_count = 0;
			_instance->executed_event_count = 0;
			_instance->ignored_event_count = 0;
			_instance->run_event_batch_count = 0;
			_instance->same_time_batch_count = 0;
			_instance->last_run_event_time = MAXIMUM_TIME;
		}
		return _instance;
	}

	void Engine::Reset()
	{
		_EventList->Clear();
		_ObjectList.clear();
		_EventStats.clear();
		_sim_time = 0;
		stop = false;
		started = false;
		event_trace_printed = 0;
		registered_event_count = 0;
		executed_event_count = 0;
		ignored_event_count = 0;
		run_event_batch_count = 0;
		same_time_batch_count = 0;
		last_run_event_time = MAXIMUM_TIME;
		Utils::Logical_Address_Partitioning_Unit::Reset();
	}


	//Add an object to the simulator object list
	void Engine::AddObject(Sim_Object* obj)
	{
		if (_ObjectList.find(obj->ID()) != _ObjectList.end()) {
			throw std::invalid_argument("Duplicate object key: " + obj->ID());
		}
		_ObjectList.insert(std::pair<sim_object_id_type, Sim_Object*>(obj->ID(), obj));
	}
	
	Sim_Object* Engine::GetObject(sim_object_id_type object_id)
	{
		auto itr = _ObjectList.find(object_id);
		if (itr == _ObjectList.end()) {
			return NULL;
		}

		return (*itr).second;
	}

	void Engine::RemoveObject(Sim_Object* obj)
	{
		std::unordered_map<sim_object_id_type, Sim_Object*>::iterator it = _ObjectList.find(obj->ID());
		if (it == _ObjectList.end()) {
			throw std::invalid_argument("Removing an unregistered object.");
		}
		_ObjectList.erase(it);
	}

		void Engine::Prepare_simulation()
		{
			if (started)
				return;
			started = true;

			for(std::unordered_map<sim_object_id_type, Sim_Object*>::iterator obj = _ObjectList.begin();
				obj != _ObjectList.end();
				++obj) {
			if (!obj->second->IsTriggersSetUp()) {
				obj->second->Setup_triggers();
			}
		}

		for (std::unordered_map<sim_object_id_type, Sim_Object*>::iterator obj = _ObjectList.begin();
			obj != _ObjectList.end();
			++obj) {
			obj->second->Validate_simulation_config();
		}
		
			for (std::unordered_map<sim_object_id_type, Sim_Object*>::iterator obj = _ObjectList.begin();
				obj != _ObjectList.end();
				++obj) {
				obj->second->Start_simulation();
			}
		}

		bool Engine::Run_next_event()
		{
			if (_EventList->Count == 0 || stop)
				return false;

			EventTreeNode* minNode = _EventList->Get_min_node();
			Sim_Event* ev = minNode->FirstSimEvent;
			sim_time_type prev_time = _sim_time;

			_sim_time = ev->Fire_time;
			run_event_batch_count++;
			if (last_run_event_time == _sim_time)
				same_time_batch_count++;
			last_run_event_time = _sim_time;

			while (ev != NULL) {
				sim_time_type delta = _sim_time >= prev_time ? _sim_time - prev_time : 0;
				record_executed_event(ev, delta);
				if(!ev->Ignore) {
					ev->Target_sim_object->Execute_simulator_event(ev);
				}
				Sim_Event* consumed_event = ev;
				ev = ev->Next_event;
				delete consumed_event;
			}
			_EventList->Remove(minNode);
			return true;
		}

		bool Engine::Has_pending_event()
		{
			return _EventList->Count > 0 && !stop;
		}

		sim_time_type Engine::Next_event_time()
		{
			if (_EventList->Count == 0)
				return MAXIMUM_TIME;
			return _EventList->Get_min_key();
		}

		void Engine::Fast_forward(sim_time_type time)
		{
			if (time > _sim_time)
				_sim_time = time;
		}

		void Engine::Enable_event_stats(uint64_t trace_first_events)
		{
			event_stats_enabled = true;
			event_trace_first = trace_first_events;
			event_trace_printed = 0;
		}

		void Engine::Print_event_stats(FILE *out, size_t top_n)
		{
			std::vector<std::pair<std::string, EventStats> > rows;

			if (!out)
				return;
			for (std::unordered_map<std::string, EventStats>::iterator it = _EventStats.begin();
			     it != _EventStats.end(); ++it)
				rows.push_back(*it);

			std::sort(rows.begin(), rows.end(),
				  [](const std::pair<std::string, EventStats>& a,
				     const std::pair<std::string, EventStats>& b) {
					  return a.second.executed > b.second.executed;
				  });

			fprintf(out, "MQSim event queue stats:\n");
			fprintf(out, "  registered=%" PRIu64 " executed=%" PRIu64
				     " ignored=%" PRIu64 " run_batches=%" PRIu64
				     " same_time_batches=%" PRIu64
				     " current_time=%" PRIu64 " next_event_time=%" PRIu64 "\n",
				registered_event_count, executed_event_count, ignored_event_count,
				run_event_batch_count, same_time_batch_count, _sim_time,
				Next_event_time());
			fprintf(out, "%48s %8s %12s %12s %12s %12s %12s %12s\n",
				"Target@Type", "Reg", "Exec", "Ignored", "AvgDelta(ns)",
				"MinDelta(ns)", "MaxDelta(ns)", "LastTime(ns)");
			fprintf(out, "================================================================================================================================\n");
			for (size_t i = 0; i < rows.size() && (top_n == 0 || i < top_n); i++) {
				const EventStats& stats = rows[i].second;
				fprintf(out, "%48s %8" PRIu64 " %12" PRIu64 " %12" PRIu64
					     " %12" PRIu64 " %12" PRIu64 " %12" PRIu64 " %12" PRIu64 "\n",
					rows[i].first.c_str(), stats.registered, stats.executed,
					stats.ignored,
					stats.executed == 0 ? 0 : stats.delta_total / stats.executed,
					stats.min_delta == MAXIMUM_TIME ? 0 : stats.min_delta,
					stats.max_delta, stats.last_time);
			}
		}

		/// This is the main method of simulator which starts simulation process.
		void Engine::Start_simulation()
		{
			Prepare_simulation();
			
			Sim_Event* ev = NULL;
			while (true) {
				if (_EventList->Count == 0 || stop) {
					break;
				}

				Run_next_event();
			}
		}

	void Engine::Stop_simulation()
	{
		stop = true;
	}

	bool Engine::Has_started()
	{
		return started;
	}

	sim_time_type Engine::Time()
	{
		return _sim_time;
	}

	Sim_Event* Engine::Register_sim_event(sim_time_type fireTime, Sim_Object* targetObject, void* parameters, int type)
	{
		Sim_Event* ev = new Sim_Event(fireTime, targetObject, parameters, type);
		DEBUG("RegisterEvent " << fireTime << " " << targetObject)
		record_registered_event(ev);
		_EventList->Insert_sim_event(ev);
		return ev;
	}

	void Engine::Ignore_sim_event(Sim_Event* ev)
	{
		ev->Ignore = true;
	}

	bool Engine::Is_integrated_execution_mode()
	{
		return false;
	}

	std::string Engine::event_key(Sim_Object *targetObject, int type)
	{
		std::string id = targetObject == NULL ? std::string("<null>") : targetObject->ID();
		return id + "@" + std::to_string(type);
	}

	void Engine::record_registered_event(Sim_Event *event)
	{
		if (!event_stats_enabled || event == NULL)
			return;
		_EventStats[event_key(event->Target_sim_object, event->Type)].registered++;
		registered_event_count++;
	}

	void Engine::record_executed_event(Sim_Event *event, sim_time_type delta)
	{
		EventStats *stats;

		if (!event_stats_enabled || event == NULL)
			return;
		stats = &_EventStats[event_key(event->Target_sim_object, event->Type)];
		stats->executed++;
		if (event->Ignore) {
			stats->ignored++;
			ignored_event_count++;
		}
		if (event->Fire_time < stats->first_time)
			stats->first_time = event->Fire_time;
		stats->last_time = event->Fire_time;
		if (delta < stats->min_delta)
			stats->min_delta = delta;
		if (delta > stats->max_delta)
			stats->max_delta = delta;
		stats->delta_total += delta;
		executed_event_count++;

		if (event_trace_printed < event_trace_first) {
			fprintf(stdout,
				"MQSim event trace: idx=%" PRIu64 " time=%" PRIu64
				" delta=%" PRIu64 " target=%s type=%d ignored=%d\n",
				executed_event_count, event->Fire_time, delta,
				event->Target_sim_object == NULL ? "<null>" :
					event->Target_sim_object->ID().c_str(),
				event->Type, event->Ignore ? 1 : 0);
			event_trace_printed++;
		}
	}
}
