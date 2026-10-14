import 'package:flutter_test/flutter_test.dart';
import 'package:mobile_app/main.dart';

void main() {
  testWidgets('City Hospital app loads successfully',
      (WidgetTester tester) async {
    await tester.pumpWidget(const CityHospitalApp());

    expect(find.text('CITY HOSPITAL'), findsOneWidget);
    expect(find.text('Your health.\nOur priority.'), findsOneWidget);
  });
}