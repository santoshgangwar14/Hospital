import 'dart:convert';



import 'package:flutter/material.dart';

import 'package:http/http.dart' as http;



void main() => runApp(const CityHospitalApp());



const navy = Color(0xFF063766);

const teal = Color(0xFF00A5B8);

const pale = Color(0xFFF5F8FC);



// Android phone and computer must be on the same Wi-Fi for this development URL.

const apiBaseUrl = 'http://192.168.29.43:8000';

const hospitalCode = 'demo-city';



class HospitalDoctor {

   const HospitalDoctor({

      required this.id,

      required this.name,

      this.department = '',

      this.qualification = '',

   });



   final String id;

   final String name;

   final String department;

   final String qualification;



   factory HospitalDoctor.fromJson(Map<String, dynamic> json) {

      return HospitalDoctor(

         id: (json['id'] ?? '').toString(),

         name: (json['name'] ?? 'Doctor').toString(),

         department: (json['department'] ?? '').toString(),

         qualification: (json['qualification'] ?? '').toString(),

      );

   }

}



class CityHospitalApp extends StatelessWidget {

   const CityHospitalApp({super.key});



   @override

   Widget build(BuildContext context) => MaterialApp(

            debugShowCheckedModeBanner: false,

            title: 'City Hospital',

            theme: ThemeData(

               useMaterial3: true,

               scaffoldBackgroundColor: pale,

               colorScheme: ColorScheme.fromSeed(seedColor: teal, primary: navy),

               appBarTheme: const AppBarTheme(

                  backgroundColor: Colors.white,

                  foregroundColor: navy,

                  elevation: 0,

                  centerTitle: false,

               ),

               inputDecorationTheme: InputDecorationTheme(

                  filled: true,

                  fillColor: Colors.white,

                  border: OutlineInputBorder(

                     borderRadius: BorderRadius.circular(12),

                     borderSide: const BorderSide(color: Color(0xFFDCE7EF)),

                  ),

                  enabledBorder: OutlineInputBorder(

                     borderRadius: BorderRadius.circular(12),

                     borderSide: const BorderSide(color: Color(0xFFDCE7EF)),

                  ),

               ),

            ),

            home: const HospitalHome(),

         );

}



class HospitalHome extends StatefulWidget {

   const HospitalHome({super.key});



   @override

   State<HospitalHome> createState() => _HospitalHomeState();

}



class _HospitalHomeState extends State<HospitalHome> {

   int selectedNav = 0;

   final patientController = TextEditingController();

   final phoneController = TextEditingController();



   @override

   void dispose() {

      patientController.dispose();

      phoneController.dispose();

      super.dispose();

   }



   void showMessage(String message) {

      if (!mounted) return;

      ScaffoldMessenger.of(context).showSnackBar(

         SnackBar(content: Text(message), behavior: SnackBarBehavior.floating),

      );

   }



   Future<List<HospitalDoctor>> fetchDoctors() async {

      final uri = Uri.parse('$apiBaseUrl/api/public/$hospitalCode/doctors');

      final response = await http.get(uri).timeout(const Duration(seconds: 15));

      if (response.statusCode != 200) {

         throw Exception('Doctor API returned HTTP ${response.statusCode}: ${response.body}');

      }



      final dynamic decoded = jsonDecode(response.body);

      final dynamic rawList = decoded is List

            ? decoded

            : decoded is Map<String, dynamic>

                  ? (decoded['doctors'] ?? decoded['items'] ?? decoded['data'] ?? [])

                  : [];

      if (rawList is! List) {

         throw Exception('Unexpected doctor API response format.');

      }



      return rawList

            .whereType<Map>()

            .map((item) => HospitalDoctor.fromJson(Map<String, dynamic>.from(item)))

            .where((doctor) => doctor.id.isNotEmpty)

            .toList();

   }



   Future<void> submitAppointment({

      required HospitalDoctor doctor,

      required DateTime date,

      required String timeSlot,

   }) async {

      final name = patientController.text.trim();

      final mobile = phoneController.text.trim();



      if (name.length < 2) {

         showMessage('Please enter patient name (at least 2 characters).');

         return;

      }

      if (!RegExp(r'^\d{10}$').hasMatch(mobile)) {

         showMessage('Please enter a valid 10-digit mobile number.');

         return;

      }



      final payload = <String, dynamic>{

         'name': name,

         'mobile': mobile,

         'doctor_id': doctor.id,

         'appt_date':
               '${date.year.toString().padLeft(4, '0')}-${date.month.toString().padLeft(2, '0')}-${date.day.toString().padLeft(2, '0')}',

         'appt_time': timeSlot,

      };


      try {

         final response = await http

               .post(

                  Uri.parse('$apiBaseUrl/api/public/$hospitalCode/appointments'),

                  headers: const {'Content-Type': 'application/json'},

                  body: jsonEncode(payload),

               )

               .timeout(const Duration(seconds: 20));



         dynamic decoded;

         try {

            decoded = jsonDecode(response.body);

         } catch (_) {

            decoded = <String, dynamic>{};

         }



         if (response.statusCode >= 200 && response.statusCode < 300) {

            final body = decoded is Map<String, dynamic> ? decoded : <String, dynamic>{};

            final id = (body['id'] ?? body['appointment_id'] ?? '').toString();

            if (!mounted) return;

            Navigator.of(context).pop();

            patientController.clear();

            phoneController.clear();

            showMessage(

               id.isNotEmpty

                     ? 'Request submitted. Booking ID: $id. Hospital confirmation is pending.'

                     : (body['message'] ?? 'Request submitted. Hospital confirmation is pending.')

                           .toString(),

            );

         } else {

            final detail = decoded is Map<String, dynamic>

                  ? (decoded['detail'] ?? decoded['message'] ?? response.body).toString()

                  : response.body;

            showMessage('Booking failed (${response.statusCode}): $detail');

         }

      } catch (error) {

         showMessage('Could not connect to hospital server. Check Wi-Fi/backend. $error');

      }

   }



   @override

   Widget build(BuildContext context) {

      final pages = <Widget>[

         _homeContent(),

         _infoPage(

            Icons.calendar_month,

            'My appointments',

            'Appointment history needs patient sign-in and a patient-specific API.',

         ),

         _infoPage(

            Icons.description_outlined,

            'Medical records',

            'Medical records require secure patient sign-in and authorized records APIs.',

         ),

         _infoPage(

            Icons.person_outline,

            'My profile',

            'Profile management requires secure patient sign-in.',

         ),

      ];



      return Scaffold(

         appBar: AppBar(

            titleSpacing: 16,

            title: const Row(

               children: [

                  DecoratedBox(

                     decoration: BoxDecoration(

                        gradient: LinearGradient(colors: [navy, teal]),

                        borderRadius: BorderRadius.all(Radius.circular(10)),

                     ),

                     child: SizedBox(

                        width: 38,

                        height: 38,

                        child: Icon(Icons.local_hospital, color: Colors.white),

                     ),

                  ),

                  SizedBox(width: 10),

                  Column(

                     crossAxisAlignment: CrossAxisAlignment.start,

                     children: [

                        Text('CITY HOSPITAL',

                              style: TextStyle(fontSize: 16, fontWeight: FontWeight.w900)),

                        Text('LUCKNOW', style: TextStyle(fontSize: 10, letterSpacing: 2)),

                     ],

                  ),

               ],

            ),

            actions: const [SizedBox(width: 8)], // API diagnostics are hidden from customers.

         ),

         body: SafeArea(

            child: IndexedStack(index: selectedNav, children: pages),

         ),

         bottomNavigationBar: NavigationBar(

            selectedIndex: selectedNav,

            onDestinationSelected: (index) => setState(() => selectedNav = index),

            destinations: const [

               NavigationDestination(

                     icon: Icon(Icons.home_outlined),

                     selectedIcon: Icon(Icons.home),

                     label: 'Home'),

               NavigationDestination(

                     icon: Icon(Icons.calendar_month_outlined),

                     selectedIcon: Icon(Icons.calendar_month),

                     label: 'Appointments'),

               NavigationDestination(

                     icon: Icon(Icons.folder_open_outlined),

                     selectedIcon: Icon(Icons.folder),

                     label: 'Records'),

               NavigationDestination(

                     icon: Icon(Icons.person_outline),

                     selectedIcon: Icon(Icons.person),

                     label: 'Profile'),

            ],

         ),

      );

   }



   Widget _homeContent() => ListView(

            padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),

            children: [

               Container(

                  padding: const EdgeInsets.all(24),

                  decoration: BoxDecoration(

                     gradient: const LinearGradient(

                        begin: Alignment.topLeft,

                        end: Alignment.bottomRight,

                        colors: [navy, Color(0xFF087CA8)],

                     ),

                     borderRadius: BorderRadius.circular(24),

                  ),

                  child: Column(

                     crossAxisAlignment: CrossAxisAlignment.start,

                     children: [

                        const Text('COMPASSION • EXPERTISE • CARE',

                              style: TextStyle(

                                    color: Color(0xFF83E7EC),

                                    fontSize: 10,

                                    fontWeight: FontWeight.w800,

                                    letterSpacing: 1.1)),

                        const SizedBox(height: 13),

                        const Text('Your health.\nOur priority.',

                              style: TextStyle(

                                    color: Colors.white,

                                    fontSize: 30,

                                    height: 1.12,

                                    fontWeight: FontWeight.w900)),

                        const SizedBox(height: 10),

                        const Text(

                           'Find specialist care and manage your hospital journey in one place.',

                           style: TextStyle(color: Color(0xFFE0EDF5), height: 1.45),

                        ),

                        const SizedBox(height: 18),

                        FilledButton.icon(

                           style: FilledButton.styleFrom(

                              backgroundColor: teal,

                              foregroundColor: Colors.white,

                              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 13),

                              shape: RoundedRectangleBorder(

                                    borderRadius: BorderRadius.circular(10)),

                           ),

                           onPressed: _showAppointmentSheet,

                           icon: const Icon(Icons.calendar_month),

                           label: const Text('Book appointment'),

                        ),

                     ],

                  ),

               ),

               const SizedBox(height: 18),

               Row(
                  children: [
                     Expanded(
                        child: _quickAction(Icons.search, 'Find a doctor', _showAppointmentSheet)),
                     const SizedBox(width: 10),
                     Expanded(
                        child: _quickAction(Icons.description_outlined, 'My records',
                           () => setState(() => selectedNav = 2))),
                     const SizedBox(width: 10),
                     Expanded(
                        child: _quickAction(Icons.emergency_outlined, 'Emergency care', () {
                           showMessage('If this is a medical emergency, please visit the nearest emergency department or contact local emergency services.');
                        })),
                  ],
               ),
               const SizedBox(height: 26),

               const Text('Care for every stage of life',

                     style: TextStyle(fontSize: 22, fontWeight: FontWeight.w900, color: navy)),

               const SizedBox(height: 5),

               const Text('Choose a department to request an appointment.',

                     style: TextStyle(color: Color(0xFF63778A))),

               const SizedBox(height: 15),

               GridView.count(

                  shrinkWrap: true,

                  physics: const NeverScrollableScrollPhysics(),

                  crossAxisCount: 2,

                  mainAxisSpacing: 12,

                  crossAxisSpacing: 12,

                  childAspectRatio: 1.42,

                  children: const [

                     ('Cardiology', Icons.favorite_outline),

                     ('Neurology', Icons.psychology_outlined),

                     ('Orthopaedics', Icons.accessibility_new),

                     ('Oncology', Icons.health_and_safety_outlined),

                     ('Women’s Health', Icons.pregnant_woman_outlined),

                     ('General Surgery', Icons.medical_services_outlined),

                     ('Pulmonology', Icons.air_outlined),

                     ('Nephrology', Icons.water_drop_outlined),

                  ].map((item) => _SpecialtyTile(

                     title: item.$1,

                     icon: item.$2,

                     onTap: _showAppointmentSheet,

                  )).toList(),

               ),

               const SizedBox(height: 24),

               Container(

                  padding: const EdgeInsets.all(17),

                  decoration: BoxDecoration(

                     color: Colors.white,

                     borderRadius: BorderRadius.circular(15),

                     border: Border.all(color: const Color(0xFFDCE7EF)),

                  ),

                  child: const Row(

                     children: [

                        Icon(Icons.shield_outlined, size: 32, color: teal),

                        SizedBox(width: 12),

                        Expanded(

                           child: Column(

                              crossAxisAlignment: CrossAxisAlignment.start,

                              children: [

                                 Text('Care with confidence',

                                       style: TextStyle(fontWeight: FontWeight.w900, color: navy)),

                                 SizedBox(height: 4),

                                 Text(

                                    'Patient records require secure sign-in before they can be accessed.',

                                    style: TextStyle(

                                          fontSize: 12, color: Color(0xFF63778A), height: 1.4),

                                 ),

                              ],

                           ),

                        ),

                     ],

                  ),

               ),

            ],

         );



   Widget _quickAction(IconData icon, String label, VoidCallback onTap) => InkWell(

            onTap: onTap,

            borderRadius: BorderRadius.circular(13),

            child: Container(

               padding: const EdgeInsets.symmetric(vertical: 15, horizontal: 8),

               decoration: BoxDecoration(

                  color: Colors.white,

                  border: Border.all(color: const Color(0xFFDCE7EF)),

                  borderRadius: BorderRadius.circular(13),

               ),

               child: Column(

                  children: [

                     Icon(icon, color: teal, size: 25),

                     const SizedBox(height: 8),

                     Text(label,

                           textAlign: TextAlign.center,

                           style: const TextStyle(

                                 fontSize: 11, fontWeight: FontWeight.w800, color: navy)),

                  ],

               ),

            ),

         );



   Widget _infoPage(IconData icon, String title, String description) => Center(

            child: Padding(

               padding: const EdgeInsets.all(28),

               child: Column(

                  mainAxisSize: MainAxisSize.min,

                  children: [

                     Icon(icon, size: 58, color: teal),

                     const SizedBox(height: 18),

                     Text(title,

                           style: const TextStyle(

                                 fontSize: 24, fontWeight: FontWeight.w900, color: navy)),

                     const SizedBox(height: 10),

                     Text(description,

                           textAlign: TextAlign.center,

                           style: const TextStyle(color: Color(0xFF63778A), height: 1.5)),

                  ],

               ),

            ),

         );



   Future<void> _showAppointmentSheet() async {

      showModalBottomSheet<void>(

         context: context,

         isScrollControlled: true,

         backgroundColor: Colors.transparent,

         builder: (sheetContext) => Padding(

            padding: EdgeInsets.only(

                  bottom: MediaQuery.of(sheetContext).viewInsets.bottom),

            child: _AppointmentSheet(

               fetchDoctors: fetchDoctors,

               patientController: patientController,

               phoneController: phoneController,

               onSubmit: submitAppointment,

            ),

         ),

      );

   }

}



class _SpecialtyTile extends StatelessWidget {

   const _SpecialtyTile({

      required this.title,

      required this.icon,

      required this.onTap,

   });



   final String title;

   final IconData icon;

   final VoidCallback onTap;



   @override

   Widget build(BuildContext context) => InkWell(

            borderRadius: BorderRadius.circular(15),

            onTap: onTap,

            child: Container(

               padding: const EdgeInsets.all(15),

               decoration: BoxDecoration(

                  color: Colors.white,

                  border: Border.all(color: const Color(0xFFDCE7EF)),

                  borderRadius: BorderRadius.circular(15),

               ),

               child: Column(

                  crossAxisAlignment: CrossAxisAlignment.start,

                  mainAxisAlignment: MainAxisAlignment.center,

                  children: [

                     Container(

                        height: 42,

                        width: 42,

                        decoration: BoxDecoration(

                           color: const Color(0xFFE5F5F8),

                           borderRadius: BorderRadius.circular(12),

                        ),

                        child: Icon(icon, color: teal, size: 23),

                     ),

                     const SizedBox(height: 12),

                     Text(title,

                           style: const TextStyle(fontWeight: FontWeight.w800, color: navy)),

                     const SizedBox(height: 3),

                     const Text('Request appointment →',

                           style: TextStyle(fontSize: 11, color: Color(0xFF63778A))),

                  ],

               ),

            ),

         );

}



class _AppointmentSheet extends StatefulWidget {

   const _AppointmentSheet({

      required this.fetchDoctors,

      required this.patientController,

      required this.phoneController,

      required this.onSubmit,

   });



   final Future<List<HospitalDoctor>> Function() fetchDoctors;

   final TextEditingController patientController;

   final TextEditingController phoneController;

   final Future<void> Function({

      required HospitalDoctor doctor,

      required DateTime date,

      required String timeSlot,

   }) onSubmit;



   @override

   State<_AppointmentSheet> createState() => _AppointmentSheetState();

}



class _AppointmentSheetState extends State<_AppointmentSheet> {

   late Future<List<HospitalDoctor>> doctorsFuture;
   HospitalDoctor? selectedDoctor;
   DateTime selectedDate = DateTime.now().add(const Duration(days: 1));
   bool submitting = false;
   List<String> availableSlots = [];
   String? selectedSlot;
   bool loadingSlots = false;
   String? slotsError;

   @override
   void initState() {
      super.initState();
      doctorsFuture = widget.fetchDoctors();
      _initializeDoctorAndSlots();
   }

   Future<void> _initializeDoctorAndSlots() async {
      try {
         final doctors = await doctorsFuture;
         if (!mounted || doctors.isEmpty) return;
         setState(() {
            selectedDoctor ??= doctors.first;
         });
         await _loadSlots();
      } catch (_) {
         // The existing doctor FutureBuilder displays doctor-loading errors.
      }
   }

   String _dateString(DateTime date) =>
       '${date.year.toString().padLeft(4, '0')}-'
       '${date.month.toString().padLeft(2, '0')}-'
       '${date.day.toString().padLeft(2, '0')}';

   String _formatSlot(String slot) {
      final parts = slot.split(':');
      if (parts.length < 2) return slot;
      final hour = int.tryParse(parts[0]);
      final minute = int.tryParse(parts[1]);
      if (hour == null || minute == null) return slot;
      final suffix = hour >= 12 ? 'PM' : 'AM';
      final displayHour = hour % 12 == 0 ? 12 : hour % 12;
      return '$displayHour:${minute.toString().padLeft(2, '0')} $suffix';
   }

   Future<void> _loadSlots() async {
      final doctor = selectedDoctor;
      if (doctor == null) return;

      setState(() {
         loadingSlots = true;
         availableSlots = [];
         selectedSlot = null;
         slotsError = null;
      });

      try {
         final uri = Uri.parse(
            '$apiBaseUrl/api/public/$hospitalCode/slots',
         ).replace(queryParameters: {
            'doctor_id': doctor.id,
            'day': _dateString(selectedDate),
         });

         final response = await http.get(uri).timeout(
            const Duration(seconds: 20),
         );

         if (response.statusCode != 200) {
            throw Exception('Slots API returned HTTP ${response.statusCode}: ${response.body}');
         }

         final decoded = jsonDecode(response.body);
         if (decoded is! Map<String, dynamic>) {
            throw Exception('Unexpected slots response format.');
         }

         final rawSlots = decoded['slots'];
         final slots = rawSlots is List
            ? rawSlots.map((slot) => slot.toString()).toList()
            : <String>[];

         if (!mounted) return;
         setState(() {
            availableSlots = slots;
            slotsError = slots.isEmpty ? 'No time slots available for this date.' : null;
         });
      } catch (_) {
         if (!mounted) return;
         setState(() {
            availableSlots = [];
            slotsError = 'Could not load slots. Check the server and try again.';
         });
      } finally {
         if (mounted) {
            setState(() => loadingSlots = false);
         }
      }
   }

   @override

   Widget build(BuildContext context) => SafeArea(

            child: Container(

               constraints: BoxConstraints(

                     maxHeight: MediaQuery.of(context).size.height * 0.88),

               padding: const EdgeInsets.fromLTRB(20, 12, 20, 24),

               decoration: const BoxDecoration(

                  color: pale,

                  borderRadius: BorderRadius.vertical(top: Radius.circular(24)),

               ),

               child: ListView(

                  shrinkWrap: true,

                  children: [

                     Center(

                        child: Container(

                           width: 42,

                           height: 4,

                           decoration: BoxDecoration(

                                 color: Colors.black26, borderRadius: BorderRadius.circular(5)),

                        ),

                     ),

                     const SizedBox(height: 18),

                     const Text('Request an appointment',

                           style: TextStyle(

                                 fontSize: 23, fontWeight: FontWeight.w900, color: navy)),

                     const SizedBox(height: 6),

                     const Text(

                        'Choose a doctor and date. The hospital will confirm your request.',

                        style: TextStyle(color: Color(0xFF63778A), fontSize: 12),

                     ),

                     const SizedBox(height: 16),

                     FutureBuilder<List<HospitalDoctor>>(

                        future: doctorsFuture,

                        builder: (context, snapshot) {

                           if (snapshot.connectionState == ConnectionState.waiting) {

                              return const Padding(

                                 padding: EdgeInsets.all(18),

                                 child: Center(child: CircularProgressIndicator()),

                              );

                           }

                           if (snapshot.hasError) {

                              return Column(

                                 crossAxisAlignment: CrossAxisAlignment.start,

                                 children: [

                                    Text('Could not load doctors: ${snapshot.error}',

                                          style: const TextStyle(color: Colors.red)),

                                    TextButton.icon(

                                       onPressed: () => setState(() {

                                          doctorsFuture = widget.fetchDoctors();

                                       }),

                                       icon: const Icon(Icons.refresh),

                                       label: const Text('Retry'),

                                    ),

                                 ],

                              );

                           }

                           final doctors = snapshot.data ?? [];

                           if (doctors.isEmpty) {

                              return const Text('No doctors are available for this hospital.');

                           }

                           selectedDoctor ??= doctors.first;

                           return DropdownButtonFormField<String>(

                              initialValue: doctors.any((d) => d.id == selectedDoctor?.id)

                                    ? selectedDoctor!.id

                                    : doctors.first.id,

                              isExpanded: true,

                              decoration: const InputDecoration(labelText: 'Select doctor'),

                              items: doctors

                                    .map((d) => DropdownMenuItem(

                                             value: d.id,

                                             child: Text(

                                                '${d.name}${d.department.isNotEmpty ? ' • ${d.department}' : ''}',

                                                overflow: TextOverflow.ellipsis,

                                             ),

                                          ))

                                    .toList(),

                              onChanged: (id) {
                                 if (id == null) return;
                                 setState(() {
                                    selectedDoctor = doctors.firstWhere((d) => d.id == id);
                                 });
                                 _loadSlots();
                              },

                           );

                        },

                     ),

                     const SizedBox(height: 12),

                     TextField(

                        controller: widget.patientController,

                        textCapitalization: TextCapitalization.words,

                        decoration: const InputDecoration(labelText: 'Patient name'),

                     ),

                     const SizedBox(height: 12),

                     TextField(

                        controller: widget.phoneController,

                        keyboardType: TextInputType.phone,

                        maxLength: 10,

                        decoration: const InputDecoration(

                              labelText: '10-digit mobile number', counterText: ''),

                     ),

                     const SizedBox(height: 12),

                     OutlinedButton.icon(

                        onPressed: () async {

                           final picked = await showDatePicker(

                              context: context,

                              initialDate: selectedDate.isBefore(DateTime.now())

                                    ? DateTime.now()

                                    : selectedDate,

                              firstDate: DateTime.now(),

                              lastDate: DateTime.now().add(const Duration(days: 365)),

                           );

                           if (picked != null && mounted) {
                              setState(() {
                                 selectedDate = picked;
                                 selectedSlot = null;
                              });
                              _loadSlots();
                           }

                        },

                        icon: const Icon(Icons.calendar_month),

                        label: Text(

                              'Appointment date: ${selectedDate.day.toString().padLeft(2, '0')}-${selectedDate.month.toString().padLeft(2, '0')}-${selectedDate.year}'),

                     ),

                     const SizedBox(height: 16),

                     const Text(
                        'Available time slots',
                        style: TextStyle(
                           fontSize: 16,
                           fontWeight: FontWeight.w800,
                           color: navy,
                        ),
                     ),

                     const SizedBox(height: 10),

                     if (loadingSlots)
                        const Padding(
                           padding: EdgeInsets.symmetric(vertical: 12),
                           child: Center(child: CircularProgressIndicator()),
                        )
                     else if (slotsError != null)
                        Column(
                           crossAxisAlignment: CrossAxisAlignment.start,
                           children: [
                              Text(
                                 slotsError!,
                                 style: const TextStyle(color: Color(0xFF63778A)),
                              ),
                              TextButton.icon(
                                 onPressed: _loadSlots,
                                 icon: const Icon(Icons.refresh),
                                 label: const Text('Retry'),
                              ),
                           ],
                        )
                     else if (availableSlots.isEmpty)
                        const Text(
                           'No time slots available for this date.',
                           style: TextStyle(color: Color(0xFF63778A)),
                        )
                     else
                        Wrap(
                           spacing: 8,
                           runSpacing: 8,
                           children: availableSlots.map((slot) {
                              final isSelected = selectedSlot == slot;
                              return ChoiceChip(
                                 label: Text(_formatSlot(slot)),
                                 selected: isSelected,
                                 onSelected: (selected) {
                                    setState(() {
                                       selectedSlot = selected ? slot : null;
                                    });
                                 },
                              );
                           }).toList(),
                        ),

                     const SizedBox(height: 16),

                     SizedBox(
                        width: double.infinity,

                        child: FilledButton(

                           style: FilledButton.styleFrom(

                              backgroundColor: teal,

                              padding: const EdgeInsets.symmetric(vertical: 15),

                           ),

                           onPressed: (submitting || loadingSlots || selectedSlot == null)
                                 ? null
                                 : () async {

                                       final doctor = selectedDoctor;

                                       if (doctor == null || selectedSlot == null) {
                                           ScaffoldMessenger.of(context).showSnackBar(

                                             const SnackBar(

                                                   content: Text('Select a doctor and available time slot first.')),

                                          );

                                          return;

                                       }

                                       setState(() => submitting = true);

                                       await widget.onSubmit(
                                              doctor: doctor,
                                              date: selectedDate,
                                              timeSlot: selectedSlot!,
                                        );

                                       if (mounted) setState(() => submitting = false);

                                    },

                           child: submitting

                                 ? const SizedBox(

                                       height: 20,

                                       width: 20,

                                       child: CircularProgressIndicator(strokeWidth: 2),

                                    )

                                 : Text(selectedSlot == null
                                      ? 'Select a time slot to continue'
                                      : 'Request appointment · ${_formatSlot(selectedSlot!)}'),

                        ),

                     ),

                  ],

               ),

            ),

         );

}
